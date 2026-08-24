"""Orphan exposure must be recoverable, not merely flattened.

An orphan is a broker position no journal group claims. Flattening it is the
correct risk action -- refusing leaves naked overnight option exposure. But if
the flatten is only logged, it is a side channel: a process dying mid-flatten
leaves an order at the venue that no restart can find, and the next run
rediscovers the position and sends a second one.

Every test here is about the RESTART, not the flatten.
"""
import datetime
import itertools
import logging

import pytest

from bujji.broker_truth.models import BrokerTruth, OpenLeg
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.production_runtime import exit_lifecycle as EL
from bujji.production_runtime import orphan_exposure as OE
from bujji.production_runtime.position_group_scope import (
    SessionScopeViolation, append_scoped_event, position_group_ids,
    session_scope_ids)
from bujji.trading_brain.risk_governor.position_group_validation import (
    IllegalEventError)

LOG = logging.getLogger("test-orphan")
SESSION = "S1"
SYMBOL = "NIFTY24000CE"
SCOPE = f"SESSION:{SESSION}"
DISCOVERED_AT = "2026-08-23T09:20:00+00:00"


class _Clock:
    def __init__(self):
        self._n = itertools.count()

    def __call__(self):
        return (datetime.datetime(2026, 8, 23, 15, 0, tzinfo=datetime.timezone.utc)
                + datetime.timedelta(seconds=next(self._n)))


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def journal(tmp_path):
    return PositionGroupJournal(str(tmp_path / "pg.db"))


def _flat():
    return BrokerTruth("CONFIRMED_FLAT", (), "empty book", "test", True)


def _open(symbol=SYMBOL, qty=75):
    return BrokerTruth("CONFIRMED_OPEN", (OpenLeg(symbol, qty, "SELL"),),
                       "holds a leg", "test", True)


def _unknown():
    return BrokerTruth("UNKNOWN", (), "read timed out", "test", True)


def _discover(journal, clock, symbol=SYMBOL, signed=-75, session=SESSION):
    return OE.record_discovery(
        journal, session_id=session, symbol=symbol, signed_quantity=signed,
        contract_id=symbol, discovered_at=DISCOVERED_AT,
        evidence_reference="broker position read, attempt 1",
        clock=clock, logger=LOG)


def _plan_orphan_exit(journal, clock, symbol=SYMBOL, qty=75, session=SESSION):
    return EL.plan(journal, session_id=session, cause=EL.CAUSE_EOD,
                   holdings=[(f"SESSION:{session}", symbol, qty)],
                   broker_truth=None, clock=clock)


# ---------------------------------------------------------------------------
# The record contract.
# ---------------------------------------------------------------------------

class TestTheRecordContract:
    def test_a_discovery_carries_everything_a_restart_needs(self, journal, clock):
        _discover(journal, clock)
        record = OE._fold_records(journal, SCOPE)[OE.orphan_id(SESSION, SYMBOL)]
        assert record.symbol == SYMBOL
        assert record.signed_quantity == -75, "the SIGN was lost"
        assert record.contract_id == SYMBOL
        assert record.session_id == SESSION
        assert record.discovered_at == DISCOVERED_AT
        assert record.evidence_reference
        assert record.broker_truth_state == "CONFIRMED_OPEN"
        assert not record.is_resolved

    def test_the_record_creates_no_position_group(self, journal, clock):
        _discover(journal, clock)
        assert position_group_ids(journal) == [], (
            "an orphan record minted a position group -- Bujji would be "
            "claiming it opened a position it has no record of")
        assert session_scope_ids(journal) == [SCOPE]

    def test_an_unsigned_quantity_is_refused(self, journal, clock):
        """The sign says whether flattening means buying or selling. Guessing
        wrong DOUBLES the exposure instead of closing it."""
        with pytest.raises(IllegalEventError, match="signed integer"):
            append_scoped_event(
                journal, SCOPE, OE.EVENT_ORPHAN, "bad:qty",
                {"orphan_id": "O1", "session_id": SESSION, "symbol": SYMBOL,
                 "signed_quantity": "75", "contract_id": SYMBOL,
                 "discovered_at": DISCOVERED_AT, "evidence_reference": "e",
                 "record_state": OE.ORPHAN_DISCOVERED,
                 "broker_truth_state": "CONFIRMED_OPEN"},
                clock=clock, logger=LOG)

    def test_a_record_with_no_evidence_reference_is_refused(self, journal, clock):
        with pytest.raises(IllegalEventError, match="evidence_reference"):
            append_scoped_event(
                journal, SCOPE, OE.EVENT_ORPHAN, "bad:ev",
                {"orphan_id": "O1", "session_id": SESSION, "symbol": SYMBOL,
                 "signed_quantity": -75, "contract_id": SYMBOL,
                 "discovered_at": DISCOVERED_AT, "evidence_reference": "",
                 "record_state": OE.ORPHAN_DISCOVERED,
                 "broker_truth_state": "CONFIRMED_OPEN"},
                clock=clock, logger=LOG)

    def test_a_naive_discovery_time_is_refused(self, journal, clock):
        with pytest.raises(IllegalEventError, match="timezone-aware"):
            append_scoped_event(
                journal, SCOPE, OE.EVENT_ORPHAN, "bad:tz",
                {"orphan_id": "O1", "session_id": SESSION, "symbol": SYMBOL,
                 "signed_quantity": -75, "contract_id": SYMBOL,
                 "discovered_at": "2026-08-23T09:20:00",
                 "evidence_reference": "e",
                 "record_state": OE.ORPHAN_DISCOVERED,
                 "broker_truth_state": "CONFIRMED_OPEN"},
                clock=clock, logger=LOG)


# ---------------------------------------------------------------------------
# Only a broker-confirmed flat resolves a record.
# ---------------------------------------------------------------------------

class TestOnlyBrokerFlatResolves:
    @pytest.mark.parametrize("broker_state",
                             ["CONFIRMED_OPEN", "UNKNOWN", "", "PROBABLY_FLAT"])
    def test_resolved_flat_is_unwritable_without_a_confirmed_flat(
            self, journal, clock, broker_state):
        """THE LIE THIS RECORD TYPE EXISTS TO MAKE UNWRITABLE: local intent, a
        swallowed exception, or "we sent the exit and heard nothing" presenting
        as a broker-confirmed flat."""
        with pytest.raises(IllegalEventError, match="CONFIRMED_FLAT"):
            append_scoped_event(
                journal, SCOPE, OE.EVENT_ORPHAN, f"bad:res:{broker_state}",
                {"orphan_id": "O1", "session_id": SESSION, "symbol": SYMBOL,
                 "signed_quantity": -75, "contract_id": SYMBOL,
                 "discovered_at": DISCOVERED_AT, "evidence_reference": "e",
                 "record_state": OE.ORPHAN_RESOLVED_FLAT,
                 "broker_truth_state": broker_state},
                clock=clock, logger=LOG)

    def test_a_confirmed_flat_does_resolve_it(self, journal, clock):
        """POSITIVE CONTROL: the refusals above must be able to pass."""
        _discover(journal, clock)
        report = OE.inspect(journal, _flat(), LOG)
        OE.resolve_absent(journal, report, _flat(), clock, LOG)
        again = OE.inspect(journal, _flat(), LOG)
        assert again.state == OE.STATE_CLEAR
        assert not again.blocks_entry
        assert [r.symbol for r in again.resolved] == [SYMBOL]

    def test_resolution_cannot_be_attempted_against_an_unreadable_book(
            self, journal, clock):
        _discover(journal, clock)
        report = OE.inspect(journal, _open(), LOG)
        with pytest.raises(OE.OrphanEvidenceCorrupt, match="UNKNOWN is not FLAT"):
            OE.resolve_absent(journal, report, _unknown(), clock, LOG)


# ---------------------------------------------------------------------------
# Startup reconciliation, and the entry gate.
# ---------------------------------------------------------------------------

class TestStartupReconciliation:
    def test_a_clean_journal_does_not_block_entry(self, journal, clock):
        report = OE.inspect(journal, _flat(), LOG)
        assert report.inspected
        assert report.state == OE.STATE_CLEAR
        assert not report.blocks_entry

    def test_an_unresolved_orphan_with_broker_UNKNOWN_blocks_entry(
            self, journal, clock):
        _discover(journal, clock)
        report = OE.inspect(journal, _unknown(), LOG)
        assert report.state == OE.STATE_UNSAFE
        assert report.blocks_entry
        assert "UNKNOWN is not FLAT" in report.detail

    def test_a_missing_broker_answer_blocks_entry(self, journal, clock):
        _discover(journal, clock)
        report = OE.inspect(journal, None, LOG)
        assert report.blocks_entry

    def test_an_orphan_the_broker_still_holds_is_resumable_not_clear(
            self, journal, clock):
        _discover(journal, clock)
        report = OE.inspect(journal, _open(), LOG)
        assert report.state == OE.STATE_RESUMED
        assert [r.symbol for r in report.resumable] == [SYMBOL]
        assert report.blocks_entry, (
            "a session traded on top of exposure it cannot account for")

    def test_an_uninspectable_journal_blocks_entry_rather_than_reporting_clear(
            self, journal, clock):
        """NEGATIVE CONTROL for the conflation that matters most: "the check
        did not run" must never reach the same decision as "the check found
        nothing"."""
        class _Unreadable:
            def read_all_group_ids(self):
                raise RuntimeError("db locked")

        report = OE.inspect(_Unreadable(), _flat(), LOG)
        assert not report.inspected
        assert report.blocks_entry
        assert report.state == OE.STATE_CLEAR, (
            "state is CLEAR yet entry is blocked -- blocks_entry must not "
            "depend on state alone")

    def test_corrupt_orphan_evidence_blocks_entry(self, journal, clock):
        """A record we cannot interpret is not an absence of exposure -- it is
        exposure we have lost the ability to describe.

        The corruption is injected at the READ boundary, not the write one:
        the validator refuses a malformed payload outright (proved by
        TestTheRecordContract above), so the only way a malformed record
        exists is a damaged row or one written before this contract existed.
        That is exactly what this simulates.
        """
        _discover(journal, clock)

        class _Event:
            event_type = OE.EVENT_ORPHAN
            payload = {"orphan_id": "O-LEGACY", "symbol": SYMBOL}

        class _DamagedRows:
            def read_all_group_ids(self):
                return journal.read_all_group_ids()

            def read_events(self, scope):
                return list(journal.read_events(scope)) + [_Event()]

        report = OE.inspect(_DamagedRows(), _flat(), LOG)
        assert not report.inspected
        assert report.blocks_entry
        assert "corrupt" in report.detail

    def test_a_record_with_no_orphan_id_on_disk_blocks_entry(self, journal, clock):
        class _Event:
            event_type = OE.EVENT_ORPHAN
            payload = {"symbol": SYMBOL, "signed_quantity": -75}

        class _DamagedRows:
            def read_all_group_ids(self):
                return [SCOPE]

            def read_events(self, scope):
                return [_Event()]

        report = OE.inspect(_DamagedRows(), _flat(), LOG)
        assert not report.inspected
        assert report.blocks_entry

    def test_an_orphan_from_a_PRIOR_session_is_still_found(self, journal, clock):
        """The whole point of scanning every session scope: an orphan is
        exactly the thing a previous process failed to finish."""
        _discover(journal, clock, session="EARLIER-SESSION")
        report = OE.inspect(journal, _open(), LOG)
        assert [r.session_id for r in report.unresolved] == ["EARLIER-SESSION"]
        assert report.blocks_entry


# ---------------------------------------------------------------------------
# Crash, restart, and the duplicate that must not happen.
# ---------------------------------------------------------------------------

class TestCrashAndRestart:
    def test_a_crash_after_discovery_leaves_the_orphan_findable(
            self, journal, clock, tmp_path):
        _discover(journal, clock)
        reopened = PositionGroupJournal(str(tmp_path / "pg.db"))
        report = OE.inspect(reopened, _open(), LOG)
        assert [r.symbol for r in report.unresolved] == [SYMBOL]

    def test_a_crash_mid_flatten_leaves_the_order_named_in_the_record(
            self, journal, clock, tmp_path):
        """The recovery property. The exit was journaled before it was sent,
        so a new process can see which order the dead one may have placed."""
        _discover(journal, clock)
        plan = _plan_orphan_exit(journal, clock)
        placed_id = plan.legs[0].broker_client_order_id
        del plan

        reopened = PositionGroupJournal(str(tmp_path / "pg.db"))
        report = OE.inspect(reopened, _open(), LOG)
        record = report.resumable[0]
        assert placed_id in record.broker_client_order_ids
        assert record.exit_attempts, "the attempt history did not travel"

    def test_a_restart_with_an_acked_unfilled_orphan_exit_does_not_duplicate(
            self, journal, clock, tmp_path):
        """THE DUPLICATE THIS EXISTS TO PREVENT. The first order may still be
        live at the venue; a second one does not close the position, it
        reverses it."""
        _discover(journal, clock)
        plan = _plan_orphan_exit(journal, clock)
        EL.record_ack(journal, plan, plan.legs[0], "BRK-1", "TRANSIT", clock)

        reopened = PositionGroupJournal(str(tmp_path / "pg.db"))
        resumed = _plan_orphan_exit(reopened, clock)
        assert resumed.legs == (), (
            "the restart planned a second exit for an orphan whose first exit "
            "is still live at the venue")

    def test_a_restart_after_an_UNKNOWN_orphan_exit_refuses_rather_than_resending(
            self, journal, clock, tmp_path):
        _discover(journal, clock)
        plan = _plan_orphan_exit(journal, clock)
        EL.record_unknown(journal, plan, plan.legs[0], "send timed out", clock)

        reopened = PositionGroupJournal(str(tmp_path / "pg.db"))
        resumed = _plan_orphan_exit(reopened, clock)
        assert resumed.legs == ()
        assert [r["target_contract_id"] for r in resumed.refused] == [SYMBOL]

    def test_a_rejected_orphan_exit_IS_retried_under_a_new_order_id(
            self, journal, clock):
        """The other half: blocking a retry after a REJECTION would leave the
        naked exposure open, which is the opposite failure."""
        _discover(journal, clock)
        first = _plan_orphan_exit(journal, clock)
        EL.record_rejection(journal, first, first.legs[0], "margin", clock)
        second = _plan_orphan_exit(journal, clock)
        assert len(second.legs) == 1
        assert (second.legs[0].broker_client_order_id
                != first.legs[0].broker_client_order_id)

    def test_resuming_never_mints_a_position_group(self, journal, clock):
        _discover(journal, clock)
        for _ in range(4):
            plan = _plan_orphan_exit(journal, clock)
            if plan.legs:
                EL.record_rejection(journal, plan, plan.legs[0], "no", clock)
        assert position_group_ids(journal) == []

    def test_a_confirmed_flat_after_the_flatten_ends_the_workflow(
            self, journal, clock):
        """End to end: discover, flatten, broker confirms flat, record settles,
        and the next startup is clear."""
        _discover(journal, clock)
        plan = _plan_orphan_exit(journal, clock)
        EL.record_ack(journal, plan, plan.legs[0], "BRK-1", "TRANSIT", clock)
        EL.record_fill(journal, plan, plan.legs[0], 75, 101.0, clock)

        report = OE.inspect(journal, _flat(), LOG)
        OE.resolve_absent(journal, report, _flat(), clock, LOG)
        final = OE.inspect(journal, _flat(), LOG)
        assert final.state == OE.STATE_CLEAR
        assert not final.blocks_entry
        assert final.unresolved == ()
        assert position_group_ids(journal) == []


# ---------------------------------------------------------------------------
# The namespace stays narrow.
# ---------------------------------------------------------------------------

class TestSessionScopeStaysNarrow:
    @pytest.mark.parametrize("event_type", [
        "MINTED", "CONSTRUCTED", "SUBMIT_INTENT", "SUBMIT_ACK", "SUBMIT_FAILURE",
        "FILL_OBSERVED", "CANCEL_INTENT", "CANCEL_ACK",
        "TARGET_GROUP_REDUCTION_APPLIED", "RECONCILIATION_ATTEMPTED",
        "FINAL_RECONCILIATION_CONFIRMED", "OPERATOR_CORRECTION_RECORDED",
    ])
    def test_no_other_position_event_may_use_session_scope(
            self, journal, clock, event_type):
        """NEGATIVE CONTROLS for the widened invariant. Two types were added to
        the session namespace; every OTHER position event must still be
        refused there, or the namespace has quietly become a place to put
        things rather than a place to put exactly these things."""
        with pytest.raises(SessionScopeViolation, match="invisible"):
            append_scoped_event(journal, SCOPE, event_type, f"bad:{event_type}",
                                {}, clock=clock, logger=LOG)

    def test_an_orphan_record_may_not_be_written_on_a_position_group(
            self, journal, clock):
        """The other direction: an ORPHAN record on a real group would claim
        the group holds exposure it never opened."""
        with pytest.raises(SessionScopeViolation, match="position group"):
            append_scoped_event(
                journal, "PG:REAL", OE.EVENT_ORPHAN, "bad:pg",
                {"orphan_id": "O1", "session_id": SESSION, "symbol": SYMBOL,
                 "signed_quantity": -75, "contract_id": SYMBOL,
                 "discovered_at": DISCOVERED_AT, "evidence_reference": "e",
                 "record_state": OE.ORPHAN_DISCOVERED,
                 "broker_truth_state": "CONFIRMED_OPEN"},
                clock=clock, logger=LOG)

    def test_the_three_permitted_types_are_exactly_those(self):
        from bujji.production_runtime.position_group_scope import (
            SESSION_PERMITTED_EVENT_TYPES)
        assert set(SESSION_PERMITTED_EVENT_TYPES) == {
            "SESSION_TRANSITION", "ORPHAN_EXPOSURE_RECORDED",
            "EXIT_ATTEMPT_RECORDED"}

    def test_exit_attempts_are_valid_in_BOTH_scopes(self, journal, clock):
        """EXIT_ATTEMPT_RECORDED is the one type legal in both: its home is the
        exposure group, and orphans have no group to call home."""
        _discover(journal, clock)
        assert _plan_orphan_exit(journal, clock).legs, (
            "an orphan exit attempt was refused by the namespace invariant")
