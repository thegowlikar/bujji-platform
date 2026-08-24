"""An exit attempt must be invisible to everything that measures exposure.

THE DEFECT THESE TESTS EXIST FOR. The first M4b draft minted a position group
per exit attempt. An acked-but-unfilled exit group folds to CONSTRUCTED, which
is in whole_book_margin_provider._ACTIVE_LIFECYCLE_STATES -- so the order
placed to REDUCE exposure was counted AS exposure and doubled the measured
book, and group enumeration returned one group per retry.

A position group means underlying exposure. An exit attempt is an event in the
life of exposure that already exists.
"""
import datetime
import itertools

import pytest

from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.production_runtime import exit_lifecycle as EL
from bujji.production_runtime.position_group_scope import position_group_ids
from bujji.trading_brain.risk_governor import whole_book_margin_provider as WBM
from bujji.trading_brain.risk_governor.position_group_fold import (
    LIFECYCLE_CLOSED, fold, net_quantity)

EXPOSURE = "PG:EXPOSURE-1"
CONTRACT = "NIFTY24000CE"
COID = "ENTRY-COID-A"
QTY = 50


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
def journal(tmp_path, clock):
    j = PositionGroupJournal(str(tmp_path / "pg.db"))
    j.append_event(EXPOSURE, "MINTED", f"{EXPOSURE}:M",
                   {"plan_id": "PLAN-1", "strategy_id": "iron_condor",
                    "underlying": "NIFTY"}, clock=clock)
    j.append_event(EXPOSURE, "CONSTRUCTED", f"{EXPOSURE}:C",
                   {"contract_client_order_map": {CONTRACT: COID},
                    "requested_quantities": {COID: QTY}}, clock=clock)
    j.append_event(EXPOSURE, "SUBMIT_INTENT", f"{EXPOSURE}:SI",
                   {"client_order_id": COID}, clock=clock)
    j.append_event(EXPOSURE, "SUBMIT_ACK", f"{EXPOSURE}:SA",
                   {"client_order_id": COID, "broker_order_id": "B1",
                    "broker_reported_status": "TRANSIT"}, clock=clock)
    j.append_event(EXPOSURE, "FILL_OBSERVED", f"{EXPOSURE}:FO",
                   {"client_order_id": COID, "cumulative_filled_quantity_after": QTY,
                    "cumulative_average_fill_price_after": 100.0,
                    "delta_quantity": QTY, "delta_value": 100.0 * QTY,
                    "delta_cost_basis_status": "DERIVED", "fill_price": 100.0},
                   clock=clock)
    return j


HOLDINGS = [(EXPOSURE, CONTRACT, QTY)]


def _plan(journal, clock, cause=EL.CAUSE_EOD, session="S1"):
    return EL.plan(journal, session_id=session, cause=cause, holdings=HOLDINGS,
                   broker_truth=None, clock=clock)


def _snapshot(journal):
    """Everything an exposure consumer can see about this group."""
    state = fold(journal.read_events(EXPOSURE))
    return {
        "groups": sorted(position_group_ids(journal)),
        "lifecycle_state": state.lifecycle_state,
        "net_quantity": {c: net_quantity(l) for c, l in state.legs.items()},
        "leg_count": len(state.legs),
        "constructed": state.constructed,
    }


class TestAttemptsCreateNoGroups:
    def test_one_attempt_creates_no_group(self, journal, clock):
        before = _snapshot(journal)
        _plan(journal, clock)
        assert _snapshot(journal)["groups"] == before["groups"] == [EXPOSURE]

    def test_ten_attempts_create_no_groups(self, journal, clock):
        """The headline. Under the old model this returned eleven groups."""
        for _ in range(10):
            plan = _plan(journal, clock)
            if plan.legs:
                EL.record_rejection(journal, plan, plan.legs[0], "margin", clock)
        assert position_group_ids(journal) == [EXPOSURE]

    def test_a_mix_of_rejected_cancelled_and_unknown_attempts_creates_no_groups(
            self, journal, clock):
        plan = _plan(journal, clock)
        EL.record_rejection(journal, plan, plan.legs[0], "margin", clock)
        plan = _plan(journal, clock)
        EL.record_cancellation(journal, plan, plan.legs[0], "timeout", clock)
        plan = _plan(journal, clock)
        EL.record_unknown(journal, plan, plan.legs[0], "read timed out", clock)
        assert position_group_ids(journal) == [EXPOSURE]

    def test_attempts_under_different_causes_still_create_no_groups(
            self, journal, clock):
        for cause in EL.ALL_CAUSES:
            plan = _plan(journal, clock, cause=cause)
            if plan.legs:
                EL.record_rejection(journal, plan, plan.legs[0], "no", clock)
        assert position_group_ids(journal) == [EXPOSURE]


class TestAttemptsDoNotMoveExposure:
    def test_an_acked_unfilled_attempt_leaves_exposure_identical(
            self, journal, clock):
        """THE EXACT OLD BUG. An acked exit group folded to CONSTRUCTED and
        was counted as active exposure, doubling the measured book."""
        before = _snapshot(journal)
        plan = _plan(journal, clock)
        EL.record_ack(journal, plan, plan.legs[0], "BRK-1", "TRANSIT", clock)
        assert _snapshot(journal) == before

    def test_every_non_fill_attempt_state_leaves_exposure_identical(
            self, journal, clock):
        before = _snapshot(journal)
        plan = _plan(journal, clock)
        EL.record_ack(journal, plan, plan.legs[0], "BRK-1", "TRANSIT", clock)
        EL.record_unknown(journal, plan, plan.legs[0], "no answer", clock)
        EL.record_reconciled(journal, plan, plan.legs[0], "absent",
                             "orderbook:2026-08-23", clock)
        assert _snapshot(journal) == before, (
            "attempt history changed what an exposure consumer sees")

    def test_no_attempt_state_reaches_an_active_margin_state(
            self, journal, clock):
        """Nothing this module writes may put a group into the margin set."""
        active = WBM._ACTIVE_LIFECYCLE_STATES
        plan = _plan(journal, clock)
        for record in (
            lambda: EL.record_ack(journal, plan, plan.legs[0], "B", "T", clock),
            lambda: EL.record_unknown(journal, plan, plan.legs[0], "x", clock),
            lambda: EL.record_rejection(journal, plan, plan.legs[0], "x", clock),
        ):
            record()
            for gid in position_group_ids(journal):
                state = fold(journal.read_events(gid))
                assert gid == EXPOSURE, f"{gid} is a synthetic group"
                # The exposure group itself is legitimately active -- it holds
                # a real position. The assertion is that NO OTHER group exists
                # to be counted alongside it.
        assert position_group_ids(journal) == [EXPOSURE]
        assert fold(journal.read_events(EXPOSURE)).lifecycle_state in active

    def test_the_fill_IS_what_moves_exposure(self, journal, clock):
        """POSITIVE CONTROL. If nothing could move exposure, the tests above
        would pass for the wrong reason."""
        before = _snapshot(journal)
        plan = _plan(journal, clock)
        EL.record_ack(journal, plan, plan.legs[0], "BRK-1", "TRANSIT", clock)
        EL.record_fill(journal, plan, plan.legs[0], QTY, 101.0, clock)
        after = _snapshot(journal)
        assert before["net_quantity"][COID] == QTY
        assert after["net_quantity"][COID] == 0
        assert after["lifecycle_state"] == LIFECYCLE_CLOSED
        assert after["groups"] == [EXPOSURE], "the fill created a group"


class TestAttemptHistoryIsOrdered:
    def test_multiple_failed_attempts_coexist_as_history(self, journal, clock):
        reasons = ["margin", "circuit limit", "session cutoff"]
        for reason in reasons:
            plan = _plan(journal, clock)
            EL.record_rejection(journal, plan, plan.legs[0], reason, clock)

        history = EL.attempt_history(journal, EXPOSURE)
        rejections = [h for h in history if h["attempt_state"] == "REJECTED"]
        assert [r["failure_reason"] for r in rejections] == reasons, (
            "attempt history was collapsed to latest-wins; the sequence of "
            "what was sent and what came back is the audit trail")
        assert len({r["exit_attempt_id"] for r in rejections}) == 3

    def test_each_attempt_has_its_own_immutable_id_and_broker_order_id(
            self, journal, clock):
        ids, coids = set(), set()
        for _ in range(3):
            plan = _plan(journal, clock)
            ids.add(plan.legs[0].exit_attempt_id)
            coids.add(plan.legs[0].broker_client_order_id)
            EL.record_rejection(journal, plan, plan.legs[0], "no", clock)
        assert len(ids) == 3, f"attempt ids collided: {ids}"
        assert len(coids) == 3, f"broker order ids collided: {coids}"

    def test_the_exposure_group_identity_never_changes(self, journal, clock):
        for _ in range(3):
            plan = _plan(journal, clock)
            assert plan.legs[0].exposure_position_group_id == EXPOSURE
            EL.record_rejection(journal, plan, plan.legs[0], "no", clock)

    def test_the_idempotency_key_carries_the_attempt_id(self, journal, clock):
        """A replayed transition must be a no-op, not a duplicate row."""
        plan = _plan(journal, clock)
        EL.record_ack(journal, plan, plan.legs[0], "BRK-1", "TRANSIT", clock)
        before = len(EL.attempt_history(journal, EXPOSURE))
        EL.record_ack(journal, plan, plan.legs[0], "BRK-1", "TRANSIT", clock)
        assert len(EL.attempt_history(journal, EXPOSURE)) == before


class TestReconstructedExposureIsUnaffected:
    def test_group_enumeration_is_identical_with_and_without_attempts(
            self, tmp_path, clock):
        """Two journals, same entry; one gets five exit attempts. Every
        exposure query must give the same answer for both."""
        def build(name, with_attempts):
            j = PositionGroupJournal(str(tmp_path / name))
            for gid in (EXPOSURE,):
                j.append_event(gid, "MINTED", f"{gid}:M",
                               {"plan_id": "P", "strategy_id": "ic",
                                "underlying": "NIFTY"}, clock=clock)
                j.append_event(gid, "CONSTRUCTED", f"{gid}:C",
                               {"contract_client_order_map": {CONTRACT: COID},
                                "requested_quantities": {COID: QTY}}, clock=clock)
                j.append_event(gid, "SUBMIT_INTENT", f"{gid}:SI",
                               {"client_order_id": COID}, clock=clock)
                j.append_event(gid, "SUBMIT_ACK", f"{gid}:SA",
                               {"client_order_id": COID, "broker_order_id": "B",
                                "broker_reported_status": "T"}, clock=clock)
                j.append_event(gid, "FILL_OBSERVED", f"{gid}:FO",
                               {"client_order_id": COID,
                                "cumulative_filled_quantity_after": QTY,
                                "cumulative_average_fill_price_after": 100.0,
                                "delta_quantity": QTY, "delta_value": 5000.0,
                                "delta_cost_basis_status": "DERIVED",
                                "fill_price": 100.0}, clock=clock)
            if with_attempts:
                for _ in range(5):
                    p = EL.plan(j, session_id="S1", cause=EL.CAUSE_EOD,
                                holdings=HOLDINGS, broker_truth=None, clock=clock)
                    if p.legs:
                        EL.record_rejection(j, p, p.legs[0], "no", clock)
            return j

        quiet = build("quiet.db", False)
        busy = build("busy.db", True)

        def exposure_view(j):
            state = fold(j.read_events(EXPOSURE))
            return (sorted(position_group_ids(j)), state.lifecycle_state,
                    {c: net_quantity(l) for c, l in state.legs.items()},
                    state.constructed, state.ever_had_any_fill)

        assert exposure_view(busy) == exposure_view(quiet), (
            "five exit attempts changed reconstructed exposure")
        assert len(EL.attempt_history(busy, EXPOSURE)) == 10
        assert EL.attempt_history(quiet, EXPOSURE) == []


class TestTheseAssertionsAreNotVacuous:
    """NEGATIVE CONTROLS. Every test above asserts that something did NOT
    happen, and a test like that passes just as happily when it is measuring
    nothing at all. These reproduce the old model's shape by hand and confirm
    the same assertions fail on it."""

    def _mint_a_decoy_exit_group(self, journal, clock, name="EXIT:S1:eod_close:A1"):
        """Exactly what the first M4b draft did per attempt."""
        journal.append_event(name, "MINTED", f"{name}:M",
                             {"plan_id": name, "strategy_id": "eod_close",
                              "underlying": "NIFTY"}, clock=clock)
        journal.append_event(name, "CONSTRUCTED", f"{name}:C",
                             {"contract_client_order_map": {CONTRACT: "EXIT-1"},
                              "requested_quantities": {"EXIT-1": QTY}}, clock=clock)
        return name

    def test_the_group_enumeration_assertion_would_catch_a_minted_group(
            self, journal, clock):
        self._mint_a_decoy_exit_group(journal, clock)
        assert position_group_ids(journal) != [EXPOSURE], (
            "position_group_ids does not see minted groups, so every "
            "no-extra-groups assertion in this file proves nothing")

    def test_the_exposure_snapshot_assertion_would_catch_a_minted_group(
            self, journal, clock):
        before = _snapshot(journal)
        self._mint_a_decoy_exit_group(journal, clock)
        assert _snapshot(journal) != before, (
            "_snapshot is blind to a new group, so every identical-exposure "
            "assertion in this file proves nothing")

    def test_a_minted_exit_group_really_does_land_in_the_margin_set(
            self, journal, clock):
        """The concrete harm, reproduced. This is what the corrected model
        stops: an exit order counted as exposure alongside the position it
        exists to close."""
        name = self._mint_a_decoy_exit_group(journal, clock)
        state = fold(journal.read_events(name))
        assert state.lifecycle_state in WBM._ACTIVE_LIFECYCLE_STATES, (
            "a constructed exit group is not in the margin-active set, so the "
            "defect this model corrects would not have had the effect claimed")
        active = [g for g in position_group_ids(journal)
                  if fold(journal.read_events(g)).lifecycle_state
                  in WBM._ACTIVE_LIFECYCLE_STATES]
        assert len(active) == 2, (
            f"expected the decoy AND the real position to both count as "
            f"active exposure, got {active}")

    def test_attempt_history_is_actually_being_written(self, journal, clock):
        """POSITIVE CONTROL: if plan() wrote nothing, every assertion that
        attempts do not disturb exposure would pass trivially."""
        assert EL.attempt_history(journal, EXPOSURE) == []
        _plan(journal, clock)
        history = EL.attempt_history(journal, EXPOSURE)
        assert len(history) == 1
        assert history[0]["attempt_state"] == "INTENT"
        assert history[0]["exposure_position_group_id"] == EXPOSURE
