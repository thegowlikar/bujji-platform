"""Tests -- Phase 15I Adaptive Position Management. Semantic fixtures,
no broker, no execution, no live calls."""
from __future__ import annotations

from bujji.position_management.engine import assess_position_management
from bujji.position_management.models import (
    RECOMMEND_ADJUST, RECOMMEND_EXIT, RECOMMEND_HEDGE, RECOMMEND_HOLD, RECOMMEND_ROLL, RECOMMEND_UNKNOWN,
    REASON_CONFLICTING_EVIDENCE, REASON_EXPOSURE_DRIFT_SEVERE,
)


class _FakeThesisEval:
    def __init__(self, thesis_status, evidence_confidence="HIGH"):
        self.thesis_status = thesis_status
        self.evidence_confidence = evidence_confidence


def _greeks(ce_delta=0.5, pe_delta=-0.5, t_years=0.05, ce_available=True, pe_available=True):
    return {
        "ce": {"available": ce_available, "delta": ce_delta if ce_available else None},
        "pe": {"available": pe_available, "delta": pe_delta if pe_available else None},
        "t_years": t_years,
    }


def _premium_behaviour(combined_direction="STEADY"):
    return {"combined": {"direction": combined_direction}}


# ---------------------------------------------------------------------------
# 1. Healthy directional position -> HOLD.
# ---------------------------------------------------------------------------
def test_healthy_position_holds():
    result = assess_position_management(
        "POS-1", "t1", _FakeThesisEval("THESIS_INTACT"),
        entry_greeks=_greeks(0.5, -0.5), current_greeks=_greeks(0.55, -0.45),
    )
    assert result.recommendation == RECOMMEND_HOLD


# ---------------------------------------------------------------------------
# 2. Weakening directional thesis -> ADJUST.
# ---------------------------------------------------------------------------
def test_weakening_thesis_adjusts():
    result = assess_position_management(
        "POS-1", "t1", _FakeThesisEval("THESIS_WEAKENING"),
        entry_greeks=_greeks(0.5, -0.5), current_greeks=_greeks(0.55, -0.45),
    )
    assert result.recommendation == RECOMMEND_ADJUST


# ---------------------------------------------------------------------------
# 3. Invalidated directional thesis -> EXIT.
# ---------------------------------------------------------------------------
def test_invalidated_thesis_exits():
    result = assess_position_management(
        "POS-1", "t1", _FakeThesisEval("THESIS_INVALIDATED"),
        entry_greeks=_greeks(0.5, -0.5), current_greeks=_greeks(0.55, -0.45),
    )
    assert result.recommendation == RECOMMEND_EXIT
    assert "REAL_REPLAY_DEFECT" not in result.reason_codes  # sanity: no bogus reason leaks in.


# ---------------------------------------------------------------------------
# 4. Excessive delta/exposure drift -> HEDGE.
# ---------------------------------------------------------------------------
def test_excessive_exposure_drift_hedges():
    # net delta bias at entry: 0.5+(-0.5)=0.0; now: 0.9+(-0.05)=0.85 -- drift 0.85 >= 0.5 threshold.
    result = assess_position_management(
        "POS-1", "t1", _FakeThesisEval("THESIS_WEAKENING"),
        entry_greeks=_greeks(0.5, -0.5), current_greeks=_greeks(0.9, -0.05),
    )
    assert result.recommendation == RECOMMEND_HEDGE
    assert REASON_EXPOSURE_DRIFT_SEVERE in result.reason_codes


def test_exposure_drift_overrides_hold_even_when_thesis_intact_conflicting_evidence():
    result = assess_position_management(
        "POS-1", "t1", _FakeThesisEval("THESIS_INTACT"),
        entry_greeks=_greeks(0.5, -0.5), current_greeks=_greeks(0.9, -0.05),
    )
    assert result.recommendation == RECOMMEND_HEDGE
    assert REASON_CONFLICTING_EVIDENCE in result.reason_codes


# ---------------------------------------------------------------------------
# 5. Viable position approaching unfavorable expiry geometry -> ROLL.
# ---------------------------------------------------------------------------
def test_imminent_expiry_rolls():
    result = assess_position_management(
        "POS-1", "t1", _FakeThesisEval("THESIS_INTACT"),
        entry_greeks=_greeks(0.5, -0.5, t_years=0.05), current_greeks=_greeks(0.55, -0.45, t_years=0.003),  # ~1 day left.
    )
    assert result.recommendation == RECOMMEND_ROLL


# ---------------------------------------------------------------------------
# 6. Insufficient evidence -> UNKNOWN.
# ---------------------------------------------------------------------------
def test_unknown_thesis_status_is_unknown():
    result = assess_position_management(
        "POS-1", "t1", _FakeThesisEval("THESIS_UNKNOWN", evidence_confidence="NONE"),
        entry_greeks=None, current_greeks=None,
    )
    assert result.recommendation == RECOMMEND_UNKNOWN


def test_none_evidence_confidence_is_unknown():
    result = assess_position_management(
        "POS-1", "t1", _FakeThesisEval("THESIS_INTACT", evidence_confidence="NONE"),
        entry_greeks=None, current_greeks=None,
    )
    assert result.recommendation == RECOMMEND_UNKNOWN


# ---------------------------------------------------------------------------
# 7. Conflicting evidence -> conservative result (never fabricated certainty).
# ---------------------------------------------------------------------------
def test_conflicting_evidence_resolves_conservatively_not_exit():
    result = assess_position_management(
        "POS-1", "t1", _FakeThesisEval("THESIS_INTACT"),
        entry_greeks=_greeks(0.5, -0.5), current_greeks=_greeks(0.95, -0.02),
    )
    assert result.recommendation != RECOMMEND_EXIT
    assert result.recommendation == RECOMMEND_HEDGE


# ---------------------------------------------------------------------------
# 8. UNKNOWN Greeks -> must not fabricate exposure.
# ---------------------------------------------------------------------------
def test_unknown_greeks_never_fabricates_exposure():
    result = assess_position_management(
        "POS-1", "t1", _FakeThesisEval("THESIS_INTACT"),
        entry_greeks=None, current_greeks=None,
    )
    exposure_evidence = next(e for e in result.evidence if e.dimension == "exposure")
    assert exposure_evidence.status == "UNKNOWN"
    assert result.recommendation == RECOMMEND_HOLD  # thesis intact, exposure genuinely unknown -- never HEDGE from nothing.


# ---------------------------------------------------------------------------
# 9. UNKNOWN premium behaviour -> must not fabricate deterioration.
# ---------------------------------------------------------------------------
def test_unknown_premium_behaviour_never_fabricates_deterioration():
    result = assess_position_management(
        "POS-1", "t1", _FakeThesisEval("THESIS_INTACT"),
        entry_greeks=_greeks(), current_greeks=_greeks(), current_premium_behaviour=None,
    )
    premium_evidence = next(e for e in result.evidence if e.dimension == "premium_behaviour")
    assert premium_evidence.status == "UNKNOWN"
    assert result.recommendation == RECOMMEND_HOLD


# ---------------------------------------------------------------------------
# 10. Repeated identical assessment -> deterministic/idempotent.
# ---------------------------------------------------------------------------
def test_repeated_identical_assessment_is_deterministic():
    args = dict(
        position_id="POS-1", evaluation_timestamp="t1",
        thesis_evaluation=_FakeThesisEval("THESIS_WEAKENING"),
        entry_greeks=_greeks(0.5, -0.5), current_greeks=_greeks(0.55, -0.45),
    )
    r1 = assess_position_management(**args)
    r2 = assess_position_management(**args)
    assert r1.to_dict() == r2.to_dict()


# ---------------------------------------------------------------------------
# 12. Multi-leg position -- evaluated as ONE position with leg-level
# exposure via the ATM-based net delta bias (already leg-aware, per
# Phase 15E's Greeks assessment covering both CE and PE legs).
# ---------------------------------------------------------------------------
def test_multi_leg_exposure_uses_both_legs():
    # Both legs available -> a real net bias computed from both, not just one.
    result = assess_position_management(
        "POS-1", "t1", _FakeThesisEval("THESIS_INTACT"),
        entry_greeks=_greeks(0.5, -0.5), current_greeks=_greeks(0.6, -0.4),
    )
    exposure_evidence = next(e for e in result.evidence if e.dimension == "exposure")
    assert exposure_evidence.status in ("NORMAL", "SEVERE_DRIFT")  # a real computed value, not UNKNOWN.


def test_exposure_unknown_when_only_one_leg_available():
    result = assess_position_management(
        "POS-1", "t1", _FakeThesisEval("THESIS_INTACT"),
        entry_greeks=_greeks(), current_greeks=_greeks(ce_available=True, pe_available=False),
    )
    exposure_evidence = next(e for e in result.evidence if e.dimension == "exposure")
    assert exposure_evidence.status == "UNKNOWN"  # one-sided data never fabricates a two-sided bias.


# ---------------------------------------------------------------------------
# Previous recommendation continuity.
# ---------------------------------------------------------------------------
def test_previous_recommendation_is_carried_through_not_rederived():
    result = assess_position_management(
        "POS-1", "t1", _FakeThesisEval("THESIS_INTACT"),
        entry_greeks=_greeks(), current_greeks=_greeks(), previous_recommendation="ADJUST",
    )
    assert result.previous_recommendation == "ADJUST"


def test_previous_recommendation_defaults_to_none():
    result = assess_position_management("POS-1", "t1", _FakeThesisEval("THESIS_INTACT"), None, None)
    assert result.previous_recommendation is None


# ---------------------------------------------------------------------------
# Reason codes / evidence traceability.
# ---------------------------------------------------------------------------
def test_every_recommendation_has_at_least_one_reason_code():
    for status in ("THESIS_INTACT", "THESIS_WEAKENING", "THESIS_INVALIDATED", "THESIS_UNKNOWN"):
        result = assess_position_management("POS-1", "t1", _FakeThesisEval(status), _greeks(), _greeks())
        assert len(result.reason_codes) >= 1


def test_evidence_covers_all_five_dimensions():
    result = assess_position_management("POS-1", "t1", _FakeThesisEval("THESIS_INTACT"), _greeks(), _greeks())
    dims = {e.dimension for e in result.evidence}
    assert dims == {"thesis", "exposure", "expiry_geometry", "premium_behaviour", "liquidity"}
