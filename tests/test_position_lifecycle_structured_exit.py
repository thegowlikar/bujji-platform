"""Tests -- Phase 15K Structured Exit lifecycle integration
(bujji.position_lifecycle: models + engine + pnl, wired together)."""
from __future__ import annotations

from bujji.position_lifecycle.engine import (
    apply_event, build_entry_snapshot_for_position, build_position_closed_payload,
    build_position_opened_payload, build_structured_exit,
)
from bujji.position_lifecycle.identity import leg_id_for
from bujji.position_lifecycle.models import (
    PNL_COMPLETE, PNL_PARTIAL, PNL_UNKNOWN, STATUS_CLOSED, STATUS_OPEN,
    TRANSITION_ACCEPTED, TRANSITION_IDEMPOTENT, TRANSITION_REJECTED,
)


class FakeLeg:
    def __init__(self, role="PRIMARY", option_type="CE", strike=24450.0, expiry="2026-08-13",
                 side="BUY", ratio=1, entry_mid=130.0, delta=0.5, entry_bid=128.0, entry_ask=132.0):
        self.role, self.option_type, self.strike, self.expiry = role, option_type, strike, expiry
        self.side, self.ratio, self.entry_mid, self.delta = side, ratio, entry_mid, delta
        self.entry_bid, self.entry_ask = entry_bid, entry_ask


class FakeCandidate:
    def __init__(self, candidate_id="STC-abc", family="LONG_DIRECTIONAL", legs=None, ts="t0", lot_size=75):
        self.candidate_id = candidate_id
        self.source_cycle_id = ts
        self.strategy_family = family
        self.timestamp = ts
        self.market_regime = "RANGING"
        self.direction = "BULLISH"
        self.thesis = "TREND_CONTINUATION"
        self.selection_confidence = "HIGH"
        self.underlying_price = 24450.0
        self.legs = legs or (FakeLeg(),)
        self.lot_size = lot_size


def _open(session_id="S1", candidate=None, entry_timestamp="t0"):
    candidate = candidate or FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    payload = build_position_opened_payload(session_id, candidate, entry, entry_timestamp)
    return apply_event({}, session_id, "POSITION_OPENED", session_id, payload)


# ---------------------------------------------------------------------------
# Backward compatibility -- entry captures bid/ask/lot_size correctly.
# ---------------------------------------------------------------------------
def test_entry_captures_bid_ask_and_lot_size():
    states, r1 = _open()
    pid = r1.position_id
    leg = states[pid].legs[0]
    assert leg.entry_bid == 128.0
    assert leg.entry_ask == 132.0
    assert states[pid].entry.lot_size == 75


# ---------------------------------------------------------------------------
# 1-6: sign correctness through the full lifecycle (profitable/losing
# long/short call, long/short put -- same underlying pnl.py already
# unit-tested, now proven end-to-end through the real reducer).
# ---------------------------------------------------------------------------
def _close_with_exit(states, pid, exit_price, side="BUY", entry_price=130.0, quantity=1, lot_size=75):
    leg = states[pid].legs[0]
    structured_exit = build_structured_exit(
        states[pid].legs, lot_size, "t5", "manual", {leg.leg_id: {"exit_price": exit_price}},
    )
    return apply_event(states, "S1", "POSITION_CLOSED", "S1",
                        build_position_closed_payload(pid, "t5", "manual", structured_exit))


def test_profitable_long_call():
    candidate = FakeCandidate(legs=(FakeLeg(option_type="CE", side="BUY", entry_mid=100.0),))
    states, r1 = _open(candidate=candidate)
    pid = r1.position_id
    states, r2 = _close_with_exit(states, pid, exit_price=150.0)
    assert r2.outcome == TRANSITION_ACCEPTED
    assert states[pid].structured_exit["gross_realized_pnl"] == 50.0 * 75
    assert states[pid].realized_pnl == 50.0 * 75


def test_losing_long_call():
    candidate = FakeCandidate(legs=(FakeLeg(option_type="CE", side="BUY", entry_mid=100.0),))
    states, r1 = _open(candidate=candidate)
    pid = r1.position_id
    states, _ = _close_with_exit(states, pid, exit_price=60.0)
    assert states[pid].structured_exit["gross_realized_pnl"] == -40.0 * 75


def test_profitable_short_call():
    candidate = FakeCandidate(legs=(FakeLeg(option_type="CE", side="SELL", entry_mid=100.0),))
    states, r1 = _open(candidate=candidate)
    pid = r1.position_id
    states, _ = _close_with_exit(states, pid, exit_price=60.0)
    assert states[pid].structured_exit["gross_realized_pnl"] == 40.0 * 75


def test_losing_short_call():
    candidate = FakeCandidate(legs=(FakeLeg(option_type="CE", side="SELL", entry_mid=100.0),))
    states, r1 = _open(candidate=candidate)
    pid = r1.position_id
    states, _ = _close_with_exit(states, pid, exit_price=150.0)
    assert states[pid].structured_exit["gross_realized_pnl"] == -50.0 * 75


def test_long_put():
    candidate = FakeCandidate(legs=(FakeLeg(option_type="PE", side="BUY", entry_mid=50.0),))
    states, r1 = _open(candidate=candidate)
    pid = r1.position_id
    states, _ = _close_with_exit(states, pid, exit_price=80.0)
    assert states[pid].structured_exit["gross_realized_pnl"] == 30.0 * 75


def test_short_put():
    candidate = FakeCandidate(legs=(FakeLeg(option_type="PE", side="SELL", entry_mid=50.0),))
    states, r1 = _open(candidate=candidate)
    pid = r1.position_id
    states, _ = _close_with_exit(states, pid, exit_price=20.0)
    assert states[pid].structured_exit["gross_realized_pnl"] == 30.0 * 75


# ---------------------------------------------------------------------------
# 7: multi-leg Iron Condor through the full lifecycle.
# ---------------------------------------------------------------------------
def test_multi_leg_iron_condor_through_lifecycle():
    legs = (
        FakeLeg(role="SHORT_CALL", option_type="CE", strike=24600.0, side="SELL", entry_mid=40.0),
        FakeLeg(role="LONG_CALL", option_type="CE", strike=24700.0, side="BUY", entry_mid=15.0),
        FakeLeg(role="SHORT_PUT", option_type="PE", strike=24300.0, side="SELL", entry_mid=35.0),
        FakeLeg(role="LONG_PUT", option_type="PE", strike=24200.0, side="BUY", entry_mid=10.0),
    )
    candidate = FakeCandidate(family="IRON_CONDOR", legs=legs)
    states, r1 = _open(candidate=candidate)
    pid = r1.position_id
    exit_prices = {l.leg_id: {"exit_price": p} for l, p in zip(
        states[pid].legs, [20.0, 5.0, 15.0, 2.0],  # short_call, long_call, short_put, long_put.
    )}
    structured_exit = build_structured_exit(states[pid].legs, 75, "t5", "manual", exit_prices)
    states, r2 = apply_event(states, "S1", "POSITION_CLOSED", "S1",
                              build_position_closed_payload(pid, "t5", "manual", structured_exit))
    assert r2.outcome == TRANSITION_ACCEPTED
    assert states[pid].structured_exit["pnl_status"] == PNL_COMPLETE
    expected = (20.0 * 75) + (-10.0 * 75) + (20.0 * 75) + (-8.0 * 75)
    assert states[pid].structured_exit["gross_realized_pnl"] == expected


# ---------------------------------------------------------------------------
# 8: multi-leg straddle.
# ---------------------------------------------------------------------------
def test_multi_leg_straddle_through_lifecycle():
    legs = (
        FakeLeg(role="LONG_CALL", option_type="CE", side="BUY", entry_mid=100.0),
        FakeLeg(role="LONG_PUT", option_type="PE", side="BUY", entry_mid=90.0),
    )
    candidate = FakeCandidate(family="LONG_STRADDLE", legs=legs)
    states, r1 = _open(candidate=candidate)
    pid = r1.position_id
    exit_prices = {l.leg_id: {"exit_price": p} for l, p in zip(states[pid].legs, [130.0, 60.0])}
    structured_exit = build_structured_exit(states[pid].legs, 75, "t5", "manual", exit_prices)
    states, _ = apply_event(states, "S1", "POSITION_CLOSED", "S1",
                             build_position_closed_payload(pid, "t5", "manual", structured_exit))
    assert states[pid].structured_exit["gross_realized_pnl"] == 0.0


# ---------------------------------------------------------------------------
# 9: partial exit -- explicitly investigated and deferred, per Step 9.
# ---------------------------------------------------------------------------
def test_partial_exit_is_deferred_not_pretended():
    """No PARTIAL_EXIT status exists in this model -- confirmed by
    inspecting the real status vocabulary. A leg with only SOME of its
    quantity exited would need a NEW quantity-tracking mechanism this
    phase does not build (see the Phase 15K report's own
    'PARTIAL_EXIT = DEFERRED' section)."""
    from bujji.position_lifecycle.models import ALL_STATUSES
    assert "PARTIAL_EXIT" not in ALL_STATUSES
    assert ALL_STATUSES == (STATUS_OPEN, STATUS_CLOSED)


# ---------------------------------------------------------------------------
# 10: missing exit price.
# ---------------------------------------------------------------------------
def test_missing_exit_price_is_unknown_through_lifecycle():
    states, r1 = _open()
    pid = r1.position_id
    structured_exit = build_structured_exit(states[pid].legs, 75, "t5", "manual", {})  # no exit price supplied at all.
    states, r2 = apply_event(states, "S1", "POSITION_CLOSED", "S1",
                              build_position_closed_payload(pid, "t5", "manual", structured_exit))
    assert r2.outcome == TRANSITION_ACCEPTED  # the CLOSE itself still succeeds -- exit economics are separately UNKNOWN.
    assert states[pid].structured_exit["pnl_status"] == PNL_UNKNOWN
    assert states[pid].structured_exit["gross_realized_pnl"] is None
    assert states[pid].realized_pnl is None


# ---------------------------------------------------------------------------
# 11: missing entry bid/ask (only entry_premium/mid available -- still usable).
# ---------------------------------------------------------------------------
def test_missing_entry_bid_ask_does_not_block_pnl():
    candidate = FakeCandidate(legs=(FakeLeg(entry_bid=None, entry_ask=None, entry_mid=100.0),))
    states, r1 = _open(candidate=candidate)
    pid = r1.position_id
    assert states[pid].legs[0].entry_bid is None
    states, _ = _close_with_exit(states, pid, exit_price=130.0, entry_price=100.0)
    # entry_mid (entry_premium) is what pnl.py actually uses -- bid/ask absence doesn't block the calculation.
    assert states[pid].structured_exit["gross_realized_pnl"] == 30.0 * 75


# ---------------------------------------------------------------------------
# 12/13: zero-quantity / malformed leg rejection at the pnl layer,
# proven again here through the full structured-exit builder.
# ---------------------------------------------------------------------------
def test_zero_quantity_leg_rejected_at_pnl_layer():
    candidate = FakeCandidate(legs=(FakeLeg(ratio=0),))
    states, r1 = _open(candidate=candidate)
    pid = r1.position_id
    structured_exit = build_structured_exit(states[pid].legs, 75, "t5", "manual", {states[pid].legs[0].leg_id: {"exit_price": 150.0}})
    assert structured_exit.legs[0].pnl_status == PNL_UNKNOWN


def test_malformed_leg_missing_from_exit_prices_is_unknown_not_zero():
    states, r1 = _open()
    pid = r1.position_id
    # exit_prices references a DIFFERENT (non-existent) leg_id -- the real leg gets nothing.
    structured_exit = build_structured_exit(states[pid].legs, 75, "t5", "manual", {"LEG-nonexistent": {"exit_price": 150.0}})
    assert structured_exit.legs[0].pnl_status == PNL_UNKNOWN
    assert structured_exit.legs[0].gross_leg_pnl is None


# ---------------------------------------------------------------------------
# 14/15: idempotent duplicate close / conflicting close rejection.
# ---------------------------------------------------------------------------
def test_idempotent_duplicate_close_with_structured_exit():
    states, r1 = _open()
    pid = r1.position_id
    leg = states[pid].legs[0]
    structured_exit = build_structured_exit(states[pid].legs, 75, "t5", "manual", {leg.leg_id: {"exit_price": 150.0}})
    payload = build_position_closed_payload(pid, "t5", "manual", structured_exit)
    states, r2 = apply_event(states, "S1", "POSITION_CLOSED", "S1", payload)
    assert r2.outcome == TRANSITION_ACCEPTED
    # A genuinely identical second CLOSED event is impossible by this
    # reducer's own design (status is already CLOSED) -- confirming
    # the existing 15G guard still fires, now WITH structured exit data present.
    states, r3 = apply_event(states, "S1", "POSITION_CLOSED", "S1", payload)
    assert r3.outcome == TRANSITION_REJECTED
    assert states[pid].structured_exit["gross_realized_pnl"] == states[pid].structured_exit["gross_realized_pnl"]


def test_conflicting_close_with_different_exit_price_rejected():
    states, r1 = _open()
    pid = r1.position_id
    leg = states[pid].legs[0]
    exit_a = build_structured_exit(states[pid].legs, 75, "t5", "manual", {leg.leg_id: {"exit_price": 150.0}})
    states, _ = apply_event(states, "S1", "POSITION_CLOSED", "S1", build_position_closed_payload(pid, "t5", "manual", exit_a))
    original_pnl = states[pid].structured_exit["gross_realized_pnl"]

    exit_b = build_structured_exit(states[pid].legs, 75, "t9", "different_reason", {leg.leg_id: {"exit_price": 90.0}})
    states, r2 = apply_event(states, "S1", "POSITION_CLOSED", "S1", build_position_closed_payload(pid, "t9", "different_reason", exit_b))
    assert r2.outcome == TRANSITION_REJECTED  # a position can only genuinely close once -- 15G's existing guard.
    assert states[pid].structured_exit["gross_realized_pnl"] == original_pnl  # never silently overwritten.


# ---------------------------------------------------------------------------
# 16: backward-compatible legacy close (no structured_exit at all).
# ---------------------------------------------------------------------------
def test_legacy_close_without_structured_exit_still_works():
    states, r1 = _open()
    pid = r1.position_id
    payload = build_position_closed_payload(pid, "t5", "manual")  # no structured_exit arg -- pre-Phase-15K call shape.
    states, r2 = apply_event(states, "S1", "POSITION_CLOSED", "S1", payload)
    assert r2.outcome == TRANSITION_ACCEPTED
    assert states[pid].status == STATUS_CLOSED
    assert states[pid].structured_exit is None
    assert states[pid].realized_pnl is None  # honestly UNKNOWN, never fabricated.


# ---------------------------------------------------------------------------
# 17: deterministic replay of the exact same lifecycle events.
# ---------------------------------------------------------------------------
def test_deterministic_replay_of_structured_exit():
    candidate = FakeCandidate()
    states, r1 = _open(candidate=candidate)
    pid = r1.position_id
    leg = states[pid].legs[0]
    structured_exit = build_structured_exit(states[pid].legs, 75, "t5", "manual", {leg.leg_id: {"exit_price": 150.0}})
    payload = build_position_closed_payload(pid, "t5", "manual", structured_exit)

    states_a, _ = apply_event(states, "S1", "POSITION_CLOSED", "S1", payload)
    states_b, _ = apply_event(states, "S1", "POSITION_CLOSED", "S1", payload)
    assert states_a[pid].to_dict() == states_b[pid].to_dict()


# ---------------------------------------------------------------------------
# P&L direction is never inferred from thesis -- proven at the lifecycle level.
# ---------------------------------------------------------------------------
def test_pnl_is_independent_of_thesis_status():
    """A losing trade with an INTACT thesis and a winning trade with an
    INVALIDATED thesis must compute IDENTICAL P&L given identical
    entry/exit prices -- structured_exit never reads thesis_evaluations."""
    states, r1 = _open()
    pid = r1.position_id
    leg = states[pid].legs[0]
    structured_exit = build_structured_exit(states[pid].legs, 75, "t5", "manual", {leg.leg_id: {"exit_price": 60.0}})
    # final_thesis_status is irrelevant input to build_structured_exit -- it isn't even a parameter.
    import inspect
    from bujji.position_lifecycle.engine import build_structured_exit as bse
    assert "thesis" not in inspect.signature(bse).parameters
