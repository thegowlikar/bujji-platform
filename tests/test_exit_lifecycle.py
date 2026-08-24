"""The nine ways an exit goes wrong, and what each one must NOT conclude.

Every scenario here is a case where the tempting answer is "flat". None of
them are. Each guard has a negative control that perturbs it and asserts the
test notices -- a guard whose test still passes with the guard removed is not
testing anything.

Exit attempts are ORDERED HISTORY ON THE EXPOSURE GROUP. That they never
create a group, move margin, or change reconstructed exposure is asserted
separately, in tests/test_exit_attempts_are_not_exposure.py.
"""
import datetime
import itertools

import pytest

from bujji.broker_truth.models import BrokerTruth, OpenLeg
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.production_runtime import exit_lifecycle as EL
from bujji.production_runtime.position_group_scope import position_group_ids
from bujji.trading_brain.risk_governor.position_group_fold import (
    LIFECYCLE_CLOSED, fold, net_quantity)

CONTRACT = "NIFTY24000CE"
CONTRACT2 = "NIFTY24000PE"


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


def _open(symbol=CONTRACT, qty=50):
    return BrokerTruth("CONFIRMED_OPEN", (OpenLeg(symbol, qty, "SELL"),),
                       "holds a leg", "test", True)


def _unknown():
    return BrokerTruth("UNKNOWN", (), "read timed out", "test", True)


def _exposure(journal, clock, group_id="PG:SRC", coid="COID-A",
              contract=CONTRACT, qty=50):
    """A real, filled exposure group -- the thing an exit closes."""
    journal.append_event(group_id, "MINTED", f"{group_id}:M",
                         {"plan_id": f"PLAN-{group_id}", "strategy_id": "iron_condor",
                          "underlying": "NIFTY"}, clock=clock)
    journal.append_event(group_id, "CONSTRUCTED", f"{group_id}:C",
                         {"contract_client_order_map": {contract: coid},
                          "requested_quantities": {coid: qty}}, clock=clock)
    journal.append_event(group_id, "SUBMIT_INTENT", f"{group_id}:SI",
                         {"client_order_id": coid}, clock=clock)
    journal.append_event(group_id, "SUBMIT_ACK", f"{group_id}:SA",
                         {"client_order_id": coid, "broker_order_id": "B1",
                          "broker_reported_status": "TRANSIT"}, clock=clock)
    journal.append_event(
        group_id, "FILL_OBSERVED", f"{group_id}:FO",
        {"client_order_id": coid, "cumulative_filled_quantity_after": qty,
         "cumulative_average_fill_price_after": 100.0, "delta_quantity": qty,
         "delta_value": 100.0 * qty, "delta_cost_basis_status": "DERIVED",
         "fill_price": 100.0}, clock=clock)
    return [(group_id, contract, qty)]


def _plan(journal, clock, holdings, cause=EL.CAUSE_EOD, session_id="S1"):
    return EL.plan(journal, session_id=session_id, cause=cause,
                   holdings=holdings, broker_truth=None, clock=clock)


class TestIntentPrecedesPlacement:
    def test_the_attempt_is_journaled_before_the_caller_can_place_it(
            self, journal, clock):
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        recorded = {h["exit_attempt_id"] for h in EL.attempt_history(journal, "PG:SRC")}
        assert {l.exit_attempt_id for l in plan.legs} <= recorded
        assert plan.legs

    def test_an_unjournalable_intent_refuses_rather_than_returning_a_plan(
            self, journal, clock):
        holdings = _exposure(journal, clock)

        class _Broken:
            def read_all_group_ids(self):
                return journal.read_all_group_ids()

            def read_events(self, gid):
                return journal.read_events(gid)

            def append_event(self, *a, **k):
                raise RuntimeError("disk full")

        with pytest.raises(EL.ExitPlanRefused, match="could not journal exit intent"):
            _plan(_Broken(), clock, holdings)

    def test_an_unreadable_journal_refuses_rather_than_reporting_nothing(
            self, journal, clock):
        """NEGATIVE CONTROL for the swallowed-read bug. Degrading an exception
        to "nothing outstanding" would let a retry re-place every leg."""
        class _Unreadable:
            def read_events(self, gid):
                raise RuntimeError("db locked")

        with pytest.raises(EL.ExitJournalUnreadable):
            EL.outstanding(_Unreadable(), "PG:SRC")

    def test_an_unrecognised_cause_is_refused(self, journal, clock):
        holdings = _exposure(journal, clock)
        with pytest.raises(EL.ExitPlanRefused, match="unrecognised exit cause"):
            EL.plan(journal, session_id="S1", cause="because", holdings=holdings,
                    broker_truth=None, clock=clock)

    def test_an_attempt_cannot_be_recorded_against_a_group_that_does_not_exist(
            self, journal, clock):
        """NEGATIVE CONTROL for the no-minting rule: if this were permitted,
        plan() could quietly create history for exposure nobody opened."""
        from bujji.trading_brain.risk_governor.position_group_validation import (
            IllegalEventError)
        with pytest.raises(IllegalEventError, match="already-minted group"):
            journal.append_event(
                "PG:GHOST", "EXIT_ATTEMPT_RECORDED", "PG:GHOST:EA:1",
                {"exit_attempt_id": "A1", "exposure_position_group_id": "PG:GHOST",
                 "broker_client_order_id": "X", "cause": EL.CAUSE_EOD,
                 "attempt_state": "INTENT", "target_contract_id": CONTRACT},
                clock=clock)


# 1. Partial exit
class TestPartialExit:
    def test_a_half_filled_exit_is_not_flat(self, journal, clock):
        holdings = _exposure(journal, clock, qty=50)
        plan = _plan(journal, clock, holdings)
        EL.record_ack(journal, plan, plan.legs[0], "B-1", "TRANSIT", clock)
        EL.record_fill(journal, plan, plan.legs[0], 25, 101.0, clock)
        outcome = EL.settle(journal, ["PG:SRC"], _open(qty=25))
        assert outcome.outcome == EL.OUTCOME_OPEN
        assert not outcome.proves_flat

    def test_the_residual_is_named_so_the_operator_knows_what_is_left(
            self, journal, clock):
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        EL.record_ack(journal, plan, plan.legs[0], "B-1", "TRANSIT", clock)
        EL.record_fill(journal, plan, plan.legs[0], 25, 101.0, clock)
        assert CONTRACT in EL.settle(journal, ["PG:SRC"], _open(qty=25)).open_symbols

    def test_a_partial_fill_leaves_the_group_open_and_reduced(self, journal, clock):
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        EL.record_ack(journal, plan, plan.legs[0], "B-1", "TRANSIT", clock)
        EL.record_fill(journal, plan, plan.legs[0], 20, 101.0, clock)
        state = fold(journal.read_events("PG:SRC"))
        assert net_quantity(state.legs["COID-A"]) == 30
        assert state.lifecycle_state != LIFECYCLE_CLOSED

    def test_a_complete_fill_closes_the_exposure_group(self, journal, clock):
        """POSITIVE CONTROL: the partial tests must be able to pass."""
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        EL.record_ack(journal, plan, plan.legs[0], "B-1", "TRANSIT", clock)
        EL.record_fill(journal, plan, plan.legs[0], 50, 101.0, clock)
        state = fold(journal.read_events("PG:SRC"))
        assert state.lifecycle_state == LIFECYCLE_CLOSED
        assert net_quantity(state.legs["COID-A"]) == 0
        assert EL.settle(journal, ["PG:SRC"], _flat()).proves_flat


# 2. Broker timeout / 8. Position-read UNKNOWN
class TestUnknownIsNeverFlat:
    def test_a_failed_position_read_settles_UNKNOWN_not_FLAT(self, journal, clock):
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        EL.record_ack(journal, plan, plan.legs[0], "B-1", "TRANSIT", clock)
        EL.record_fill(journal, plan, plan.legs[0], 50, 101.0, clock)
        outcome = EL.settle(journal, ["PG:SRC"], _unknown())
        assert outcome.outcome == EL.OUTCOME_UNKNOWN
        assert not outcome.proves_flat, (
            "every local record said the exit filled -- still not evidence the "
            "account is flat")

    def test_a_missing_broker_answer_settles_UNKNOWN(self, journal, clock):
        _exposure(journal, clock)
        assert EL.settle(journal, ["PG:SRC"], None).outcome == EL.OUTCOME_UNKNOWN

    def test_an_unreadable_journal_settles_UNKNOWN_even_when_broker_is_flat(
            self, journal, clock):
        class _Unreadable:
            def read_events(self, gid):
                raise RuntimeError("db locked")
        assert EL.settle(_Unreadable(), ["PG:SRC"], _flat()).outcome == EL.OUTCOME_UNKNOWN

    def test_a_timed_out_send_leaves_the_attempt_unresolved(self, journal, clock):
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        EL.record_unknown(journal, plan, plan.legs[0], "send timed out", clock)
        _, unresolved = EL.outstanding(journal, "PG:SRC")
        assert CONTRACT in unresolved
        assert not EL.settle(journal, ["PG:SRC"], _flat()).proves_flat


# 3. Acknowledged but unfilled
class TestAcknowledgedIsNotFilled:
    def test_an_acked_exit_with_no_fill_does_not_prove_flat(self, journal, clock):
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        EL.record_ack(journal, plan, plan.legs[0], "B-1", "TRANSIT", clock)
        outcome = EL.settle(journal, ["PG:SRC"], _flat())
        assert not outcome.proves_flat
        assert outcome.outcome == EL.OUTCOME_UNKNOWN

    def test_an_acked_exit_is_not_re_sent(self, journal, clock):
        """ACK means the order is live at the venue. Another one is how a
        flatten becomes a reversal."""
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        EL.record_ack(journal, plan, plan.legs[0], "B-1", "TRANSIT", clock)
        assert _plan(journal, clock, holdings).legs == ()


# 4. Duplicate retry
class TestRetriesNeverDuplicate:
    def test_a_rejected_attempt_is_retried_under_new_ids(self, journal, clock):
        holdings = _exposure(journal, clock)
        first = _plan(journal, clock, holdings)
        EL.record_rejection(journal, first, first.legs[0], "margin", clock)
        second = _plan(journal, clock, holdings)
        assert len(second.legs) == 1
        assert second.legs[0].exit_attempt_id != first.legs[0].exit_attempt_id
        assert (second.legs[0].broker_client_order_id
                != first.legs[0].broker_client_order_id)
        assert second.legs[0].exposure_position_group_id == "PG:SRC", (
            "the retry moved to a different position group")

    def test_an_unresolved_attempt_is_refused_not_re_sent(self, journal, clock):
        holdings = _exposure(journal, clock)
        first = _plan(journal, clock, holdings)
        second = _plan(journal, clock, holdings)
        assert second.legs == ()
        assert [r["target_contract_id"] for r in second.refused] == [CONTRACT]
        assert second.refused[0]["prior_exit_attempt_id"] == first.legs[0].exit_attempt_id

    def test_one_unresolved_leg_does_not_block_flattening_its_sibling(
            self, journal, clock):
        """Refusing the plan would leave MORE exposure open, not less."""
        holdings = _exposure(journal, clock)
        holdings += _exposure(journal, clock, group_id="PG:SRC2", coid="COID-B",
                              contract=CONTRACT2)
        EL.plan(journal, session_id="S1", cause=EL.CAUSE_EOD,
                holdings=holdings[:1], broker_truth=None, clock=clock)
        second = _plan(journal, clock, holdings)
        assert [l.target_contract_id for l in second.legs] == [CONTRACT2]
        assert [r["target_contract_id"] for r in second.refused] == [CONTRACT]

    def test_a_filled_leg_is_never_exited_twice(self, journal, clock):
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        EL.record_ack(journal, plan, plan.legs[0], "B-1", "TRANSIT", clock)
        EL.record_fill(journal, plan, plan.legs[0], 50, 101.0, clock)
        assert _plan(journal, clock, holdings).legs == ()

    def test_reconciliation_is_the_only_way_out_of_UNKNOWN(self, journal, clock):
        holdings = _exposure(journal, clock)
        first = _plan(journal, clock, holdings)
        EL.record_unknown(journal, first, first.legs[0], "no answer", clock)
        assert _plan(journal, clock, holdings).legs == ()

        EL.record_reconciled(journal, first, first.legs[0], "absent from book",
                             "orderbook-dump:2026-08-23T15:20Z", clock)
        retry = _plan(journal, clock, holdings)
        assert len(retry.legs) == 1
        assert retry.legs[0].exit_attempt_id != first.legs[0].exit_attempt_id

    def test_reconciling_without_evidence_is_refused(self, journal, clock):
        """NEGATIVE CONTROL: an assertion with nothing behind it is how an
        unresolved order becomes a duplicate."""
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        EL.record_unknown(journal, plan, plan.legs[0], "no answer", clock)
        with pytest.raises(EL.ExitPlanRefused, match="requires evidence"):
            EL.record_reconciled(journal, plan, plan.legs[0], "assumed gone", "",
                                 clock)

    def test_an_attempt_with_no_target_contract_blocks_planning(
            self, journal, clock):
        """NEGATIVE CONTROL for the attribution guard. An attempt this module
        cannot attribute may be the very order that already closed the leg."""
        _exposure(journal, clock)
        journal.append_event(
            "PG:SRC", "EXIT_ATTEMPT_RECORDED", "PG:SRC:EA:orphan",
            {"exit_attempt_id": "ORPHAN", "exposure_position_group_id": "PG:SRC",
             "broker_client_order_id": "X", "cause": EL.CAUSE_EOD,
             "attempt_state": "INTENT", "target_contract_id": ""}, clock=clock)
        with pytest.raises(EL.ExitJournalUnreadable, match="no target contract"):
            EL.outstanding(journal, "PG:SRC")


# 5. Crash during exit / 6. Restart during exit
class TestCrashAndRestart:
    def test_a_crash_after_intent_leaves_the_attempt_named_in_the_journal(
            self, journal, clock, tmp_path):
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        attempt_id = plan.legs[0].exit_attempt_id
        del plan

        reopened = PositionGroupJournal(str(tmp_path / "pg.db"))
        _, unresolved = EL.outstanding(reopened, "PG:SRC")
        assert unresolved == {CONTRACT: attempt_id}

    def test_a_restart_mid_exit_does_not_re_place_the_acked_leg(
            self, journal, clock, tmp_path):
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        EL.record_ack(journal, plan, plan.legs[0], "B-1", "TRANSIT", clock)
        reopened = PositionGroupJournal(str(tmp_path / "pg.db"))
        assert _plan(reopened, clock, holdings).legs == ()

    def test_a_restart_sees_the_same_exposure_group_it_left(
            self, journal, clock, tmp_path):
        holdings = _exposure(journal, clock)
        for _ in range(3):
            plan = _plan(journal, clock, holdings)
            if plan.legs:
                EL.record_rejection(journal, plan, plan.legs[0], "no", clock)
        reopened = PositionGroupJournal(str(tmp_path / "pg.db"))
        assert position_group_ids(reopened) == ["PG:SRC"]

    def test_a_crash_before_any_intent_leaves_nothing_outstanding(
            self, journal, clock, tmp_path):
        """POSITIVE CONTROL: outstanding() must be able to report empty."""
        _exposure(journal, clock)
        reopened = PositionGroupJournal(str(tmp_path / "pg.db"))
        assert EL.outstanding(reopened, "PG:SRC") == ({}, {})


# 7. Failed cancellation
class TestFailedCancellation:
    def test_an_unconfirmed_cancel_leaves_the_attempt_unresolved(
            self, journal, clock):
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        EL.record_ack(journal, plan, plan.legs[0], "B-1", "TRANSIT", clock)
        EL.record_unknown(journal, plan, plan.legs[0], "cancel not confirmed", clock)
        assert not EL.settle(journal, ["PG:SRC"], _flat()).proves_flat, (
            "a cancel requested but never confirmed is not a cancelled order "
            "-- the exit may still fill")
        assert _plan(journal, clock, holdings).legs == ()

    def test_a_confirmed_cancellation_is_terminal_and_permits_a_retry(
            self, journal, clock):
        holdings = _exposure(journal, clock)
        first = _plan(journal, clock, holdings)
        EL.record_ack(journal, first, first.legs[0], "B-1", "TRANSIT", clock)
        EL.record_cancellation(journal, first, first.legs[0], "confirmed gone", clock)
        second = _plan(journal, clock, holdings)
        assert len(second.legs) == 1
        assert second.legs[0].exit_attempt_id != first.legs[0].exit_attempt_id


# 9. EOD / emergency overlap
class TestOverlappingClosures:
    def test_an_emergency_close_does_not_re_exit_what_EOD_already_acked(
            self, journal, clock):
        """Two causes, one account. The second must see the first's orders."""
        holdings = _exposure(journal, clock)
        eod = _plan(journal, clock, holdings, cause=EL.CAUSE_EOD)
        EL.record_ack(journal, eod, eod.legs[0], "B-1", "TRANSIT", clock)
        EL.record_fill(journal, eod, eod.legs[0], 50, 101.0, clock)
        emergency = _plan(journal, clock, holdings, cause=EL.CAUSE_EMERGENCY)
        assert emergency.legs == (), (
            "the emergency path planned an exit for a holding EOD already "
            "closed -- that order OPENS a position rather than closing one")

    def test_a_strategy_exit_and_an_abort_flatten_do_not_double_up(
            self, journal, clock):
        holdings = _exposure(journal, clock)
        strategy = _plan(journal, clock, holdings, cause=EL.CAUSE_STRATEGY)
        EL.record_ack(journal, strategy, strategy.legs[0], "B-1", "TRANSIT", clock)
        assert _plan(journal, clock, holdings, cause=EL.CAUSE_ABORT).legs == ()

    def test_overlap_is_seen_across_different_session_ids(self, journal, clock):
        """Attempts live on the EXPOSURE, so a differently-labelled session
        cannot be blind to what is already live at the venue."""
        holdings = _exposure(journal, clock)
        first = _plan(journal, clock, holdings, session_id="S1")
        EL.record_ack(journal, first, first.legs[0], "B-1", "TRANSIT", clock)
        assert _plan(journal, clock, holdings, session_id="S2").legs == ()

    def test_the_cause_is_recorded_on_every_attempt(self, journal, clock):
        holdings = _exposure(journal, clock)
        _plan(journal, clock, holdings, cause=EL.CAUSE_ABORT)
        causes = {h["cause"] for h in EL.attempt_history(journal, "PG:SRC")}
        assert causes == {EL.CAUSE_ABORT}

    def test_all_four_causes_share_one_path(self):
        assert EL.ALL_CAUSES == (EL.CAUSE_EOD, EL.CAUSE_EMERGENCY,
                                 EL.CAUSE_STRATEGY, EL.CAUSE_ABORT)


class TestExitedRequiresBrokerProof:
    def test_proves_flat_requires_the_broker_AND_the_journal(self, journal, clock):
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        EL.record_ack(journal, plan, plan.legs[0], "B-1", "TRANSIT", clock)
        EL.record_fill(journal, plan, plan.legs[0], 50, 101.0, clock)
        assert EL.settle(journal, ["PG:SRC"], _flat()).proves_flat

    def test_a_broker_flat_alone_does_not_prove_flat(self, journal, clock):
        """NEGATIVE CONTROL: perturbing the journal half must flip it."""
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        EL.record_ack(journal, plan, plan.legs[0], "B-1", "TRANSIT", clock)
        assert not EL.settle(journal, ["PG:SRC"], _flat()).proves_flat

    def test_a_settled_journal_alone_does_not_prove_flat(self, journal, clock):
        """NEGATIVE CONTROL for the other half."""
        holdings = _exposure(journal, clock)
        plan = _plan(journal, clock, holdings)
        EL.record_ack(journal, plan, plan.legs[0], "B-1", "TRANSIT", clock)
        EL.record_fill(journal, plan, plan.legs[0], 50, 101.0, clock)
        assert not EL.settle(journal, ["PG:SRC"], _unknown()).proves_flat

    def test_the_only_outcome_a_caller_may_call_exited_is_CONFIRMED_FLAT(self):
        for outcome in (EL.OUTCOME_OPEN, EL.OUTCOME_UNKNOWN):
            assert not EL.ExitOutcome(outcome=outcome).proves_flat
        assert EL.ExitOutcome(outcome=EL.OUTCOME_FLAT).proves_flat
