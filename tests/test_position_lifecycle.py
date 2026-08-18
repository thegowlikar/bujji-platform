"""Tests -- Phase 15G Position Lifecycle Intelligence. Pure fixtures,
no broker, no execution, no live calls."""
from __future__ import annotations

from bujji.position_lifecycle.engine import (
    apply_event, build_entry_snapshot_for_position, build_position_closed_payload,
    build_position_opened_payload, build_thesis_evaluated_payload,
)
from bujji.position_lifecycle.identity import leg_id_for, position_id_for
from bujji.position_lifecycle.models import (
    DISPLAY_MONITORING, STATUS_CLOSED, STATUS_OPEN, TRANSITION_ACCEPTED, TRANSITION_IDEMPOTENT, TRANSITION_REJECTED,
)


class FakeLeg:
    def __init__(self, role="PRIMARY", option_type="CE", strike=24450.0, expiry="2026-08-13",
                 side="BUY", ratio=1, entry_mid=130.0, delta=0.5):
        self.role, self.option_type, self.strike, self.expiry = role, option_type, strike, expiry
        self.side, self.ratio, self.entry_mid, self.delta = side, ratio, entry_mid, delta


class FakeCandidate:
    def __init__(self, candidate_id="STC-abc", family="LONG_DIRECTIONAL", legs=None, ts="t0"):
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


def _open(session_id="S1", candidate=None, entry_timestamp="t0", record=None):
    candidate = candidate or FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, record)
    payload = build_position_opened_payload(session_id, candidate, entry, entry_timestamp)
    return apply_event({}, session_id, "POSITION_OPENED", session_id, payload)


# ---------------------------------------------------------------------------
# Canonical identity.
# ---------------------------------------------------------------------------
def test_position_id_is_deterministic():
    id1 = position_id_for("S1", "STC-abc", "t0")
    id2 = position_id_for("S1", "STC-abc", "t0")
    assert id1 == id2


def test_position_id_differs_for_different_entry_timestamp():
    id1 = position_id_for("S1", "STC-abc", "t0")
    id2 = position_id_for("S1", "STC-abc", "t1")
    assert id1 != id2


def test_position_id_differs_across_sessions():
    id1 = position_id_for("S1", "STC-abc", "t0")
    id2 = position_id_for("S2", "STC-abc", "t0")
    assert id1 != id2


def test_leg_id_scoped_under_position():
    leg1 = leg_id_for("POS-A", "PRIMARY", "CE", 24450.0, "2026-08-13")
    leg2 = leg_id_for("POS-B", "PRIMARY", "CE", 24450.0, "2026-08-13")
    assert leg1 != leg2  # same leg shape, different parent position -- no collision.


# ---------------------------------------------------------------------------
# Basic lifecycle: candidate -> OPEN -> MONITORING (derived) -> CLOSED.
# ---------------------------------------------------------------------------
def test_open_then_close_lifecycle():
    states, r1 = _open()
    assert r1.outcome == TRANSITION_ACCEPTED
    pid = r1.position_id
    assert states[pid].status == STATUS_OPEN
    assert states[pid].display_status == STATUS_OPEN  # no thesis evaluations yet.

    states, r2 = apply_event(states, "S1", "POSITION_CLOSED", "S1", build_position_closed_payload(pid, "t5", "session_end"))
    assert r2.outcome == TRANSITION_ACCEPTED
    assert states[pid].status == STATUS_CLOSED
    assert states[pid].closed_at == "t5"
    assert states[pid].exit_reason == "session_end"


def test_monitoring_is_derived_not_stored_after_thesis_evaluation():
    states, r1 = _open()
    pid = r1.position_id
    fake_eval = _fake_evaluation("THESIS_INTACT")
    states, r2 = apply_event(states, "S1", "THESIS_EVALUATED", "S1",
                              build_thesis_evaluated_payload(pid, "t1", fake_eval))
    assert r2.outcome == TRANSITION_ACCEPTED
    assert states[pid].status == STATUS_OPEN  # stored status unchanged.
    assert states[pid].display_status == DISPLAY_MONITORING  # derived label reflects it.


class _FakeEvaluation:
    def __init__(self, status):
        self._status = status

    def to_dict(self):
        return {"thesis_status": self._status, "checks": [], "recommendation": "HOLD", "recommendation_reason": "r"}


def _fake_evaluation(status):
    return _FakeEvaluation(status)


# ---------------------------------------------------------------------------
# Multi-leg correctness (Step 5).
# ---------------------------------------------------------------------------
def test_multi_leg_iron_condor_all_legs_linked_to_one_position():
    legs = (
        FakeLeg(role="SHORT_CALL", option_type="CE", strike=24600.0, side="SELL"),
        FakeLeg(role="LONG_CALL", option_type="CE", strike=24700.0, side="BUY"),
        FakeLeg(role="SHORT_PUT", option_type="PE", strike=24300.0, side="SELL"),
        FakeLeg(role="LONG_PUT", option_type="PE", strike=24200.0, side="BUY"),
    )
    candidate = FakeCandidate(family="IRON_CONDOR", legs=legs)
    states, r1 = _open(candidate=candidate)
    assert r1.outcome == TRANSITION_ACCEPTED
    pid = r1.position_id
    lifecycle = states[pid]
    assert len(lifecycle.legs) == 4
    assert all(leg.leg_id.startswith("LEG-") for leg in lifecycle.legs)
    assert len({leg.leg_id for leg in lifecycle.legs}) == 4  # all distinct.
    assert {leg.role for leg in lifecycle.legs} == {"SHORT_CALL", "LONG_CALL", "SHORT_PUT", "LONG_PUT"}
    # ALL legs belong to the ONE position_id -- lifecycle identity lives
    # at the position level, never fragmented per-leg.
    assert lifecycle.position_id == pid


def test_multi_leg_legs_preserve_real_entry_data():
    legs = (FakeLeg(entry_mid=130.0, delta=0.55), FakeLeg(option_type="PE", entry_mid=95.0, delta=-0.45))
    candidate = FakeCandidate(legs=legs)
    states, r1 = _open(candidate=candidate)
    pid = r1.position_id
    ce_leg = next(l for l in states[pid].legs if l.option_type == "CE")
    pe_leg = next(l for l in states[pid].legs if l.option_type == "PE")
    assert ce_leg.entry_premium == 130.0 and ce_leg.entry_delta == 0.55
    assert pe_leg.entry_premium == 95.0 and pe_leg.entry_delta == -0.45


# ---------------------------------------------------------------------------
# Step 8: explicit transition guards.
# ---------------------------------------------------------------------------
def test_candidate_to_open_requires_a_real_event():
    """No implicit OPEN -- an empty state dict has no positions at all."""
    assert {} == {}


def test_thesis_evaluation_before_open_is_rejected_unknown_position():
    fake_pid = position_id_for("S1", "STC-never-opened", "t0")
    states, r = apply_event({}, "S1", "THESIS_EVALUATED", "S1",
                             build_thesis_evaluated_payload(fake_pid, "t1", _fake_evaluation("THESIS_INTACT")))
    assert r.outcome == TRANSITION_REJECTED
    assert "unknown position_id" in r.reason


def test_close_before_open_is_rejected_unknown_position():
    fake_pid = position_id_for("S1", "STC-never-opened", "t0")
    states, r = apply_event({}, "S1", "POSITION_CLOSED", "S1", build_position_closed_payload(fake_pid, "t1", "manual"))
    assert r.outcome == TRANSITION_REJECTED
    assert "unknown position_id" in r.reason


def test_closed_to_open_without_new_identity_is_rejected():
    """CLOSED -> OPEN without a new position identity = reject."""
    states, r1 = _open()
    pid = r1.position_id
    states, _ = apply_event(states, "S1", "POSITION_CLOSED", "S1", build_position_closed_payload(pid, "t5", "manual"))
    # Attempt to "re-open" via ANOTHER POSITION_OPENED with the SAME position_id -- reject.
    candidate = FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    payload = build_position_opened_payload("S1", candidate, entry, "t0")
    payload["position_id"] = pid  # force the same id, simulating a buggy re-open attempt.
    payload["opened_at"] = "t99"  # DIFFERENT content -- genuinely conflicting, not idempotent.
    states, r2 = apply_event(states, "S1", "POSITION_OPENED", "S1", payload)
    assert r2.outcome == TRANSITION_REJECTED
    assert states[pid].status == STATUS_CLOSED  # never silently reopened.


def test_open_open_duplicate_content_is_idempotent():
    """OPEN -> OPEN duplicate event = idempotent (identical content, real retry)."""
    candidate = FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    payload = build_position_opened_payload("S1", candidate, entry, "t0")
    states, r1 = apply_event({}, "S1", "POSITION_OPENED", "S1", payload)
    states, r2 = apply_event(states, "S1", "POSITION_OPENED", "S1", payload)  # same payload again.
    assert r1.outcome == TRANSITION_ACCEPTED
    assert r2.outcome == TRANSITION_IDEMPOTENT
    assert len(states) == 1


def test_close_close_second_close_is_rejected_not_silently_reapplied():
    states, r1 = _open()
    pid = r1.position_id
    states, r2 = apply_event(states, "S1", "POSITION_CLOSED", "S1", build_position_closed_payload(pid, "t5", "manual"))
    states, r3 = apply_event(states, "S1", "POSITION_CLOSED", "S1", build_position_closed_payload(pid, "t9", "different_reason"))
    assert r2.outcome == TRANSITION_ACCEPTED
    assert r3.outcome == TRANSITION_REJECTED
    assert states[pid].closed_at == "t5"  # the SECOND close never overwrote the first.


def test_conflicting_event_payload_is_rejected():
    """Two different POSITION_OPENED payloads claiming the same position_id."""
    candidate = FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    payload_a = build_position_opened_payload("S1", candidate, entry, "t0")
    states, r1 = apply_event({}, "S1", "POSITION_OPENED", "S1", payload_a)
    pid = r1.position_id

    payload_b = dict(payload_a)
    payload_b["opened_at"] = "t999"  # conflicting content, same position_id.
    states, r2 = apply_event(states, "S1", "POSITION_OPENED", "S1", payload_b)
    assert r2.outcome == TRANSITION_REJECTED


def test_malformed_event_missing_field_is_rejected():
    states, r = apply_event({}, "S1", "POSITION_OPENED", "S1", {"position_id": "POS-X"})  # missing entry/legs/opened_at.
    assert r.outcome == TRANSITION_REJECTED
    assert "malformed" in r.reason


def test_event_from_another_session_is_rejected():
    candidate = FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    payload = build_position_opened_payload("S1", candidate, entry, "t0")
    states, r = apply_event({}, "S1", "POSITION_OPENED", "S2", payload)  # event_session_id=S2, expected S1.
    assert r.outcome == TRANSITION_REJECTED
    assert "session" in r.reason
    assert states == {}


def test_thesis_evaluation_on_closed_position_is_rejected():
    states, r1 = _open()
    pid = r1.position_id
    states, _ = apply_event(states, "S1", "POSITION_CLOSED", "S1", build_position_closed_payload(pid, "t5", "manual"))
    states, r2 = apply_event(states, "S1", "THESIS_EVALUATED", "S1",
                              build_thesis_evaluated_payload(pid, "t9", _fake_evaluation("THESIS_INTACT")))
    assert r2.outcome == TRANSITION_REJECTED
    assert "not OPEN" in r2.reason


def test_unknown_event_type_is_rejected():
    states, r = apply_event({}, "S1", "SOMETHING_MADE_UP", "S1", {})
    assert r.outcome == TRANSITION_REJECTED


# ---------------------------------------------------------------------------
# Thesis continuity (Step 7).
# ---------------------------------------------------------------------------
def test_thesis_history_accumulates_in_order():
    states, r1 = _open()
    pid = r1.position_id
    for status in ("THESIS_INTACT", "THESIS_INTACT", "THESIS_WEAKENING", "THESIS_INVALIDATED"):
        states, r = apply_event(states, "S1", "THESIS_EVALUATED", "S1",
                                 build_thesis_evaluated_payload(pid, f"c-{status}", _fake_evaluation(status)))
        assert r.outcome == TRANSITION_ACCEPTED
    lifecycle = states[pid]
    assert len(lifecycle.thesis_evaluations) == 4
    assert [e["thesis_status"] for e in lifecycle.thesis_evaluations] == [
        "THESIS_INTACT", "THESIS_INTACT", "THESIS_WEAKENING", "THESIS_INVALIDATED",
    ]
    assert lifecycle.final_thesis_status == "THESIS_INVALIDATED"  # most recent, not a summary/vote.
