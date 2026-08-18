"""Phase 20.1 -- mic_v0.risk_classifier tests."""
from __future__ import annotations

from bujji.intelligence.event_brain import VIX_ELEVATED_THRESHOLD, VIX_LOW_THRESHOLD
from bujji.mic_v0.models import RISK_ELEVATED, RISK_EXTREME, RISK_NORMAL
from bujji.mic_v0.risk_classifier import VIX_EXTREME_THRESHOLD, classify_risk_state


def test_low_vix_is_normal():
    state, _ = classify_risk_state(VIX_LOW_THRESHOLD - 1.0)
    assert state == RISK_NORMAL


def test_moderate_vix_is_normal_not_elevated():
    midpoint = (VIX_LOW_THRESHOLD + VIX_ELEVATED_THRESHOLD) / 2
    state, _ = classify_risk_state(midpoint)
    assert state == RISK_NORMAL


def test_elevated_vix_is_elevated():
    state, _ = classify_risk_state(VIX_ELEVATED_THRESHOLD + 1.0)
    assert state == RISK_ELEVATED


def test_extreme_vix_is_extreme():
    state, _ = classify_risk_state(VIX_EXTREME_THRESHOLD + 5.0)
    assert state == RISK_EXTREME


def test_boundary_at_extreme_threshold_is_extreme():
    state, _ = classify_risk_state(VIX_EXTREME_THRESHOLD)
    assert state == RISK_EXTREME


def test_evidence_never_cites_macro_event_calendar():
    """"event_brain" appearing as a code-provenance citation (this
    module reuses ITS threshold constants) is expected and fine --
    what must never appear is a MACRO calendar event (RBI/Budget/
    elections) used as classification evidence, which is exactly the
    thing this phase's charter forbids until a real event module
    exists."""
    forbidden_macro_terms = ("rbi", "budget", "election", "mpc", "macro event", "scheduled event")
    for vix in (10.0, 15.0, 25.0, 35.0):
        _, evidence = classify_risk_state(vix)
        joined = " ".join(evidence).lower()
        for term in forbidden_macro_terms:
            assert term not in joined
