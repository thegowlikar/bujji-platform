"""Deterministic failure injection across the eight ways a session goes wrong.

Each scenario drives the REAL journal and the REAL derivation, injects one
failure, and asserts the lifecycle owner reaches a state that is either
correct or UNKNOWN -- never a confident wrong answer.

The rule every scenario tests is the same one: a session that cannot establish
what it is may not take new risk.
"""
from __future__ import annotations

import datetime as _dt
import logging

import pytest

from bujji.broker_truth import flat, open_with, unknown
from bujji.broker_truth.models import OpenLeg
from bujji.core.clock import IST
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.production_runtime.session_lifecycle import (
    UNKNOWN, reconstruct, record_transition)
from bujji.production_runtime.trading_session_governor.session_trading_state import (
    TradingSessionState as TSS)

CLOCK = lambda: _dt.datetime(2026, 8, 24, 9, 30, tzinfo=IST)   # noqa: E731
LOG = logging.getLogger("test")
SESSION = "S-FI"
CE, PE = "NSE:NIFTY2690124500CE", "NSE:NIFTY2690124000PE"


def _j(tmp_path, name):
    return PositionGroupJournal(str(tmp_path / name))


def _t(j, *pairs):
    for prior, nxt, cause in pairs:
        record_transition(j, SESSION, prior, nxt, cause=cause,
                          evidence_ref=f"ref:{cause}", clock=CLOCK, logger=LOG)


def _mint(j, group, legs):
    coids = {sym: f"{group}-LEG-{i}" for i, sym in enumerate(legs)}
    j.append_event(group, "MINTED", f"{group}:M",
                   {"plan_id": "p", "strategy_id": "S", "underlying": "NIFTY"}, clock=CLOCK)
    j.append_event(group, "CONSTRUCTED", f"{group}:C", {
        "contract_client_order_map": {s: c for s, c in coids.items()},
        "requested_quantities": {c: 65 for c in coids.values()},
        "actions": {c: "SELL" for c in coids.values()},
        "target_position_group_ids": {}, "target_contract_ids": {},
        "flip_link_ids": {}}, clock=CLOCK)
    return coids


def _submit(j, group, coid):
    j.append_event(group, "SUBMIT_INTENT", f"{group}:SI:{coid}",
                   {"client_order_id": coid}, clock=CLOCK)


def _ack_fill(j, group, coid, qty=65):
    """Intent -> ack -> fill. The journal enforces this ordering, so a helper
    that skips the intent is testing a sequence production never writes."""
    _submit(j, group, coid)
    j.append_event(group, "SUBMIT_ACK", f"{group}:SA:{coid}",
                   {"client_order_id": coid, "broker_order_id": f"B-{coid}",
                    "broker_reported_status": "FILLED"}, clock=CLOCK)
    j.append_event(group, "FILL_OBSERVED", f"{group}:F:{coid}", {
        "client_order_id": coid, "cumulative_filled_quantity_after": qty,
        "cumulative_average_fill_price_after": 23.1, "delta_quantity": qty,
        "delta_value": 23.1 * qty, "delta_cost_basis_status": "DERIVED",
        "fill_price": 23.1}, clock=CLOCK)


_OPEN_BOTH = open_with([OpenLeg(CE, 65), OpenLeg(PE, 65)], "held", "paper", True)
_OPEN_ONE = open_with([OpenLeg(CE, 65)], "held", "paper", True)
_FLAT = flat("no open legs", "paper", True)
_UNKNOWN = unknown("socket closed", "paper")


# ==========================================================================
# 1. CRASH DURING ENTRY -- submitted, never acked.
# ==========================================================================

def test_crash_during_entry_with_the_broker_holding_it(tmp_path):
    """The order went out and the process died before the ack. The journal
    says STRATEGY_LOCKED; the broker holds a leg."""
    j = _j(tmp_path, "c1.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"))
    coids = _mint(j, "PG-1", [CE])
    _submit(j, "PG-1", coids[CE])

    got = reconstruct(j, SESSION, _OPEN_ONE)
    assert got.is_unknown, "exposure the journal does not know about must be UNKNOWN"
    assert not got.permits_entry


def test_crash_after_submit_intent_is_UNKNOWN_even_when_the_broker_is_flat(tmp_path):
    """CORRECTED 2026-08-23. This test previously asserted STRATEGY_LOCKED and
    permitted a retry, on the reasoning that "nothing filled, so the day is
    not spent". That was wrong, and it was the most dangerous kind of wrong:
    it permitted a SECOND order while a first one of unknown fate may still
    have been live.

    POSITION TRUTH IS NOT ORDER TRUTH. A flat position book says what the
    account holds at the instant it was read. It says nothing about an order
    working at the exchange, one whose acknowledgement was never seen, or one
    that fills a second later. A SUBMIT_INTENT with no terminal outcome is
    exactly that order.
    """
    j = _j(tmp_path, "c2.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"))
    coids = _mint(j, "PG-1", [CE])
    _submit(j, "PG-1", coids[CE])

    got = reconstruct(j, SESSION, _FLAT)
    assert got.is_unknown, "a submitted order of unknown fate must block"
    assert not got.permits_entry
    assert "no terminal fate" in got.reason
    assert got.evidence["unresolved_orders"][0]["submit_status"] == "SUBMIT_PENDING_UNKNOWN"


# -- THE TWO CASES THAT MAY RETURN TO STRATEGY_LOCKED -----------------------
#
# Broker-flat permits a retry ONLY when the event history proves no submit
# intent occurred, or order truth proves every submitted order reached a
# terminal absent / cancelled / rejected state.

def test_no_submit_intent_ever_occurred_permits_a_retry(tmp_path):
    """Case 1: the group was minted and constructed, and nothing was ever
    sent. There is no order to be uncertain about."""
    j = _j(tmp_path, "c3.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"))
    _mint(j, "PG-1", [CE])                       # constructed, never submitted

    got = reconstruct(j, SESSION, _FLAT)
    assert got.state is TSS.STRATEGY_LOCKED
    assert got.permits_entry
    assert got.evidence["unresolved_orders"] == []


def test_a_rejected_order_is_terminal_and_permits_a_retry(tmp_path):
    """Case 2a: the venue rejected it. Its fate is settled."""
    j = _j(tmp_path, "c4.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"))
    coids = _mint(j, "PG-1", [CE])
    _submit(j, "PG-1", coids[CE])
    j.append_event("PG-1", "SUBMIT_FAILURE", "PG-1:SF", {
        "client_order_id": coids[CE], "failure_reason": "REJECTED_BY_VENUE",
        "resolution_basis": "CONFIRMED_REJECTION"}, clock=CLOCK)

    got = reconstruct(j, SESSION, _FLAT)
    assert got.state is TSS.STRATEGY_LOCKED
    assert got.permits_entry, "a rejected order left no exposure and no doubt"


def test_a_cancelled_order_is_terminal_and_permits_a_retry(tmp_path):
    """Case 2b: cancelled, confirmed."""
    j = _j(tmp_path, "c5.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"))
    coids = _mint(j, "PG-1", [CE])
    _submit(j, "PG-1", coids[CE])
    j.append_event("PG-1", "SUBMIT_ACK", "PG-1:SA", {
        "client_order_id": coids[CE], "broker_order_id": "B",
        "broker_reported_status": "PENDING"}, clock=CLOCK)
    j.append_event("PG-1", "CANCEL_INTENT", "PG-1:CI",
                   {"client_order_id": coids[CE]}, clock=CLOCK)
    j.append_event("PG-1", "CANCEL_ACK", "PG-1:CA",
                   {"client_order_id": coids[CE]}, clock=CLOCK)

    got = reconstruct(j, SESSION, _FLAT)
    assert got.state is TSS.STRATEGY_LOCKED
    assert got.permits_entry


def test_an_ACKED_order_with_no_fill_is_still_working_and_blocks(tmp_path):
    """The subtle one. The venue acknowledged the order -- so it is LIVE, not
    absent -- and it simply has not filled yet. A flat book read an instant
    ago does not settle that."""
    j = _j(tmp_path, "c6.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"))
    coids = _mint(j, "PG-1", [CE])
    _submit(j, "PG-1", coids[CE])
    j.append_event("PG-1", "SUBMIT_ACK", "PG-1:SA", {
        "client_order_id": coids[CE], "broker_order_id": "B",
        "broker_reported_status": "PENDING"}, clock=CLOCK)

    got = reconstruct(j, SESSION, _FLAT)
    assert got.is_unknown, "an acknowledged, unfilled order is a working order"
    assert not got.permits_entry
    assert "ACKED" in got.reason


def test_one_unresolved_leg_blocks_even_when_the_others_are_terminal(tmp_path):
    """Partial resolution is not resolution."""
    j = _j(tmp_path, "c7.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"))
    coids = _mint(j, "PG-1", [CE, PE])
    _submit(j, "PG-1", coids[CE])
    j.append_event("PG-1", "SUBMIT_FAILURE", "PG-1:SF", {
        "client_order_id": coids[CE], "failure_reason": "REJECTED",
        "resolution_basis": "CONFIRMED_REJECTION"}, clock=CLOCK)
    _submit(j, "PG-1", coids[PE])                # this one's fate is open

    got = reconstruct(j, SESSION, _FLAT)
    assert got.is_unknown
    assert len(got.evidence["unresolved_orders"]) == 1


# ==========================================================================
# 2. PARTIAL ENTRY -- one leg of two filled.
# ==========================================================================

def test_partial_entry_is_unknown_when_the_broker_disagrees(tmp_path):
    j = _j(tmp_path, "p1.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"),
       (TSS.STRATEGY_LOCKED, TSS.POSITION_ACTIVE, "entry_filled"))
    coids = _mint(j, "PG-1", [CE, PE])
    _ack_fill(j, "PG-1", coids[CE])
    _submit(j, "PG-1", coids[PE])            # second leg submitted, never filled

    assert reconstruct(j, SESSION, _FLAT).is_unknown, (
        "the journal says a position is active and the broker holds nothing")


def test_partial_entry_agrees_when_the_broker_holds_the_filled_leg(tmp_path):
    j = _j(tmp_path, "p2.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"),
       (TSS.STRATEGY_LOCKED, TSS.POSITION_ACTIVE, "entry_filled"))
    coids = _mint(j, "PG-1", [CE, PE])
    _ack_fill(j, "PG-1", coids[CE])

    got = reconstruct(j, SESSION, _OPEN_ONE)
    assert got.state is TSS.POSITION_ACTIVE
    assert not got.permits_entry


# ==========================================================================
# 3. PARTIAL EXIT -- one leg closed, one still live.
# ==========================================================================

def test_partial_exit_still_reads_as_a_live_position(tmp_path):
    j = _j(tmp_path, "x1.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"),
       (TSS.STRATEGY_LOCKED, TSS.POSITION_ACTIVE, "entry_filled"),
       (TSS.POSITION_ACTIVE, TSS.MANAGING, "lifecycle_evaluation"))
    coids = _mint(j, "PG-1", [CE, PE])
    _ack_fill(j, "PG-1", coids[CE])
    _ack_fill(j, "PG-1", coids[PE])

    got = reconstruct(j, SESSION, _OPEN_ONE)     # one leg closed at the broker
    assert got.state is TSS.MANAGING
    assert not got.permits_entry


def test_a_partial_exit_reported_as_exited_is_unknown(tmp_path):
    """Claiming EXITED while the broker still holds a leg is the phantom flat
    this whole layer exists to refuse."""
    j = _j(tmp_path, "x2.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"),
       (TSS.STRATEGY_LOCKED, TSS.POSITION_ACTIVE, "f"),
       (TSS.POSITION_ACTIVE, TSS.EXITED, "exit_confirmed"))
    _mint(j, "PG-1", [CE, PE])

    got = reconstruct(j, SESSION, _OPEN_ONE)
    assert got.is_unknown
    assert CE in got.reason


# ==========================================================================
# 4. EMERGENCY CLOSE.
# ==========================================================================

def test_emergency_close_that_confirms_flat(tmp_path):
    j = _j(tmp_path, "e1.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"),
       (TSS.STRATEGY_LOCKED, TSS.POSITION_ACTIVE, "f"),
       (TSS.POSITION_ACTIVE, TSS.EXITED, "emergency_close:confirmed_flat"))

    got = reconstruct(j, SESSION, _FLAT)
    assert got.state is TSS.EXITED
    assert "emergency_close" in got.transitions[-1]["cause"]


def test_emergency_close_that_did_not_flatten_is_unknown(tmp_path):
    j = _j(tmp_path, "e2.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"),
       (TSS.STRATEGY_LOCKED, TSS.POSITION_ACTIVE, "f"),
       (TSS.POSITION_ACTIVE, TSS.EXITED, "emergency_close:confirmed_flat"))
    assert reconstruct(j, SESSION, _OPEN_BOTH).is_unknown


# ==========================================================================
# 5. EOD CLOSE.
# ==========================================================================

def test_eod_close_completes_the_session(tmp_path):
    j = _j(tmp_path, "d1.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"),
       (TSS.STRATEGY_LOCKED, TSS.POSITION_ACTIVE, "f"),
       (TSS.POSITION_ACTIVE, TSS.EXITED, "eod_close"),
       (TSS.EXITED, TSS.SESSION_COMPLETE, "session_end"))

    got = reconstruct(j, SESSION, _FLAT)
    assert got.state is TSS.SESSION_COMPLETE
    assert not got.permits_entry


def test_eod_close_with_an_unresolved_position_is_unknown(tmp_path):
    """EOD reports unresolved positions; it never force-closes. A session that
    ended SESSION_COMPLETE while the broker still holds legs cannot be
    reconciled, and must not read as a clean finish."""
    j = _j(tmp_path, "d2.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"),
       (TSS.STRATEGY_LOCKED, TSS.POSITION_ACTIVE, "f"),
       (TSS.POSITION_ACTIVE, TSS.SESSION_COMPLETE, "session_end_unresolved"))
    assert reconstruct(j, SESSION, _OPEN_BOTH).is_unknown


# ==========================================================================
# 6. RESTART.
# ==========================================================================

def test_a_restart_rebuilds_the_whole_chain(tmp_path):
    j = _j(tmp_path, "r1.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"),
       (TSS.STRATEGY_LOCKED, TSS.POSITION_ACTIVE, "f"))
    _mint(j, "PG-1", [CE])

    # A NEW process: nothing in memory, only the journal and the broker.
    reopened = PositionGroupJournal(str(tmp_path / "r1.db"))
    got = reconstruct(reopened, SESSION, _OPEN_ONE)
    assert got.state is TSS.POSITION_ACTIVE
    assert len(got.transitions) == 3
    assert [t["next_state"] for t in got.transitions] == [
        "ANALYSING_MARKET", "STRATEGY_LOCKED", "POSITION_ACTIVE"]


def test_a_restart_on_a_completed_day_cannot_re_enter(tmp_path):
    j = _j(tmp_path, "r2.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"),
       (TSS.STRATEGY_LOCKED, TSS.POSITION_ACTIVE, "f"),
       (TSS.POSITION_ACTIVE, TSS.EXITED, "exit_confirmed"))

    got = reconstruct(PositionGroupJournal(str(tmp_path / "r2.db")), SESSION, _FLAT)
    assert got.already_deployed and not got.permits_entry


# ==========================================================================
# 7. BROKER UNKNOWN.
# ==========================================================================

@pytest.mark.parametrize("chain", [
    [(TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s")],
    [(TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
     (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l")],
    [(TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
     (TSS.ANALYSING_MARKET, TSS.STRATEGY_LOCKED, "l"),
     (TSS.STRATEGY_LOCKED, TSS.POSITION_ACTIVE, "f")],
])
def test_broker_unknown_blocks_from_every_state(tmp_path, chain):
    """UNKNOWN is not FLAT, and it is not OPEN. From any state, an
    unestablished account blocks entry."""
    j = _j(tmp_path, f"u{len(chain)}.db")
    _t(j, *chain)
    got = reconstruct(j, SESSION, _UNKNOWN)
    assert got.is_unknown and not got.permits_entry


# ==========================================================================
# 8. EVENT-JOURNAL CORRUPTION.
# ==========================================================================

def test_a_corrupt_position_history_is_unknown(tmp_path):
    class _Corrupt:
        def read_events(self, gid):
            if str(gid).startswith("SESSION:"):
                class _E:
                    event_type = "SESSION_TRANSITION"
                    payload = {"session_id": SESSION, "prior_state": "INITIALIZING",
                               "next_state": "STRATEGY_LOCKED", "cause": "l",
                               "evidence_ref": "r"}
                    recorded_at = CLOCK()
                    sequence_no = 0
                    event_id = "e"
                return [_E()]
            raise OSError("database disk image is malformed")

        def read_all_group_ids(self):
            return ["PG-1"]

    got = reconstruct(_Corrupt(), SESSION, _FLAT)
    assert got.is_unknown and not got.permits_entry


def test_a_truncated_transition_chain_is_unknown(tmp_path):
    """The middle of the chain is gone -- events lost to a partial write."""
    j = _j(tmp_path, "t1.db")
    _t(j, (TSS.INITIALIZING, TSS.ANALYSING_MARKET, "s"),
       (TSS.POSITION_ACTIVE, TSS.EXITED, "exit"))
    assert reconstruct(j, SESSION, _FLAT).is_unknown


def test_an_empty_journal_is_unknown_not_a_fresh_session(tmp_path):
    """The dangerous one. 'No history' must not read as 'nothing happened
    yet' -- that is how a restarted process re-enters."""
    got = reconstruct(_j(tmp_path, "t2.db"), SESSION, _FLAT)
    assert got.is_unknown and not got.permits_entry


def test_an_unreadable_order_history_is_reported_not_swallowed():
    """DIRECT, because it is otherwise unreachable through the journal:
    `_position_evidence` reads the same groups and catches the failure first,
    so a negative control on the order-fate error path came back silent.

    The path exists because the two reads could diverge -- a scoped view, a
    different journal object, a partial failure -- and an order history that
    cannot be read must never present as "no unresolved orders".
    """
    from bujji.production_runtime.session_lifecycle import unresolved_order_fates

    class _Unreadable:
        def read_all_group_ids(self):
            return ["PG-1"]

        def read_events(self, gid):
            raise OSError("database disk image is malformed")

    unresolved, error = unresolved_order_fates(_Unreadable())
    assert error, "an unreadable order history must report an error"
    assert unresolved == [], "and must not claim it found nothing"


def test_a_readable_history_with_no_orders_reports_no_error(tmp_path):
    """The positive control: 'no unresolved orders' and 'could not look' must
    be distinguishable."""
    from bujji.production_runtime.session_lifecycle import unresolved_order_fates

    unresolved, error = unresolved_order_fates(_j(tmp_path, "ok.db"))
    assert unresolved == [] and error is None
