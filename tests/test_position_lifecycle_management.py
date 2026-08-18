"""Tests -- Phase 15I MANAGEMENT_ASSESSED lifecycle integration
(bujji.position_lifecycle + bujji.position_management). Confirms the
new event type follows the exact same guard discipline as
THESIS_EVALUATED/POSITION_CLOSED (Phase 15G), and never implies an
adjustment/hedge/roll actually happened."""
from __future__ import annotations

from bujji.position_lifecycle.engine import (
    apply_event, build_entry_snapshot_for_position, build_management_assessed_payload,
    build_position_closed_payload, build_position_opened_payload,
)
from bujji.position_lifecycle.models import DISPLAY_MONITORING, STATUS_CLOSED, STATUS_OPEN, TRANSITION_ACCEPTED, TRANSITION_REJECTED
from bujji.position_management.engine import assess_position_management


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


class _FakeThesisEval:
    def __init__(self, thesis_status, evidence_confidence="HIGH"):
        self.thesis_status = thesis_status
        self.evidence_confidence = evidence_confidence


def _open(session_id="S1", candidate=None, entry_timestamp="t0"):
    candidate = candidate or FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    payload = build_position_opened_payload(session_id, candidate, entry, entry_timestamp)
    return apply_event({}, session_id, "POSITION_OPENED", session_id, payload)


def _assess_and_apply(states, session_id, pid, cycle_id, thesis_status="THESIS_INTACT"):
    assessment = assess_position_management(pid, cycle_id, _FakeThesisEval(thesis_status), None, None)
    payload = build_management_assessed_payload(pid, cycle_id, assessment)
    return apply_event(states, session_id, "MANAGEMENT_ASSESSED", session_id, payload), assessment


# ---------------------------------------------------------------------------
# Basic recording.
# ---------------------------------------------------------------------------
def test_management_assessment_recorded_while_open():
    states, r1 = _open()
    pid = r1.position_id
    (states, r2), assessment = _assess_and_apply(states, "S1", pid, "c1")
    assert r2.outcome == TRANSITION_ACCEPTED
    assert len(states[pid].management_assessments) == 1
    assert states[pid].latest_management_recommendation == assessment.recommendation
    assert states[pid].status == STATUS_OPEN  # NEVER mutates status -- advisory only.


def test_management_assessment_never_implies_action_taken():
    """Recording a HEDGE/ADJUST/ROLL/EXIT recommendation must not
    change the position's own canonical status or legs -- it is
    evidence, not an action."""
    states, r1 = _open()
    pid = r1.position_id
    legs_before = states[pid].legs
    (states, r2), assessment = _assess_and_apply(states, "S1", pid, "c1", thesis_status="THESIS_INVALIDATED")
    assert assessment.recommendation == "EXIT"  # a strong recommendation was made...
    assert states[pid].status == STATUS_OPEN     # ...but the position is still OPEN -- nothing executed.
    assert states[pid].legs == legs_before


# ---------------------------------------------------------------------------
# Required scenario: already-closed position -> no management recommendation.
# ---------------------------------------------------------------------------
def test_already_closed_position_rejects_management_assessment():
    states, r1 = _open()
    pid = r1.position_id
    states, _ = apply_event(states, "S1", "POSITION_CLOSED", "S1", build_position_closed_payload(pid, "t5", "manual"))
    (states, r2), assessment = _assess_and_apply(states, "S1", pid, "c1")
    assert r2.outcome == TRANSITION_REJECTED
    assert "not OPEN" in r2.reason
    assert states[pid].management_assessments == ()  # nothing was recorded.


# ---------------------------------------------------------------------------
# Required scenario: wrong-session position -> rejected.
# ---------------------------------------------------------------------------
def test_management_assessment_from_another_session_is_rejected():
    states, r1 = _open(session_id="S1")
    pid = r1.position_id
    assessment = assess_position_management(pid, "c1", _FakeThesisEval("THESIS_INTACT"), None, None)
    payload = build_management_assessed_payload(pid, "c1", assessment)
    states2, r2 = apply_event(states, "S1", "MANAGEMENT_ASSESSED", "S2", payload)  # event tagged for S2, expected S1.
    assert r2.outcome == TRANSITION_REJECTED
    assert "session" in r2.reason
    assert states2[pid].management_assessments == ()


def test_management_assessment_unknown_position_id_rejected():
    assessment = assess_position_management("POS-nope", "c1", _FakeThesisEval("THESIS_INTACT"), None, None)
    payload = build_management_assessed_payload("POS-nope", "c1", assessment)
    states, r = apply_event({}, "S1", "MANAGEMENT_ASSESSED", "S1", payload)
    assert r.outcome == TRANSITION_REJECTED
    assert "unknown position_id" in r.reason


# ---------------------------------------------------------------------------
# Required scenario: multi-leg position -- evaluated as ONE position.
# ---------------------------------------------------------------------------
def test_multi_leg_position_gets_one_management_assessment_history():
    legs = (
        FakeLeg(role="SHORT_CALL", option_type="CE", strike=24600.0, side="SELL"),
        FakeLeg(role="SHORT_PUT", option_type="PE", strike=24300.0, side="SELL"),
    )
    candidate = FakeCandidate(family="IRON_CONDOR", legs=legs)
    states, r1 = _open(candidate=candidate)
    pid = r1.position_id
    assert len(states[pid].legs) == 2

    (states, r2), assessment = _assess_and_apply(states, "S1", pid, "c1", thesis_status="THESIS_WEAKENING")
    assert r2.outcome == TRANSITION_ACCEPTED
    # ONE assessment history, at the POSITION level, not per-leg.
    assert len(states[pid].management_assessments) == 1
    assert len(states[pid].legs) == 2  # legs remain untouched, still both present.


# ---------------------------------------------------------------------------
# Accumulation / MONITORING display status still works with management events.
# ---------------------------------------------------------------------------
def test_multiple_assessments_accumulate_in_order():
    states, r1 = _open()
    pid = r1.position_id
    recommendations = []
    for i, status in enumerate(("THESIS_INTACT", "THESIS_WEAKENING", "THESIS_INVALIDATED")):
        (states, r), assessment = _assess_and_apply(states, "S1", pid, f"c{i}", thesis_status=status)
        assert r.outcome == TRANSITION_ACCEPTED
        recommendations.append(assessment.recommendation)
    assert len(states[pid].management_assessments) == 3
    assert states[pid].latest_management_recommendation == recommendations[-1]
    # display_status is derived from thesis_evaluations (Phase 15G's
    # own original design), not management_assessments -- this test
    # only recorded MANAGEMENT_ASSESSED events, so it correctly stays OPEN.
    assert states[pid].display_status == STATUS_OPEN
