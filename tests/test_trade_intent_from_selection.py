"""Tests -- determine_trade_intent_from_selection, Phase 14B P0.1. Pure
functions, real minimal fixture objects (no adapter), no broker, no
execution."""
from __future__ import annotations

from bujji.msi_decision_synthesis.models import MarketOpportunityAssessment
from bujji.msi_decision_synthesis import taxonomy as dse_taxonomy
from bujji.msi_strategy_eligibility.models import Explanation as SeiExplanation, StrategyEligibilityAssessment
from bujji.msi_strategy_eligibility import taxonomy as sei_taxonomy
from bujji.msi_trade_intent.engine import determine_trade_intent, determine_trade_intent_from_selection
from bujji.msi_trade_intent import taxonomy as ti_taxonomy


def _mk_opportunity(**overrides):
    defaults = dict(
        assessment_id="dse-1", timestamp="2026-07-25T09:00:00",
        opportunity_state=dse_taxonomy.OPPORTUNITY_STATE_DIRECTIONAL,
        confidence_level=dse_taxonomy.CONFIDENCE_HIGH, opportunity_quality=dse_taxonomy.QUALITY_GOOD,
        supporting_domains=(dse_taxonomy.DOMAIN_PRICE_STRUCTURE,), conflicting_domains=(),
        compatible_strategy_families=(), incompatible_strategy_families=(),
        evidence_ids=("E1",), episode_ids=("EP1",),
        provenance="msi_decision_synthesis.engine.synthesize", schema_version=dse_taxonomy.RECOGNIZED_SCHEMA_VERSIONS[0],
    )
    defaults.update(overrides)
    return MarketOpportunityAssessment(**defaults)


def _mk_eligibility(eligible_strategy_families, eligibility_confidence=sei_taxonomy.ELIGIBILITY_CONFIDENCE_HIGH, assessment_id="sei-1"):
    ineligible = tuple(sorted(f for f in sei_taxonomy.ALL_STRATEGY_FAMILIES if f not in eligible_strategy_families))
    explanation = SeiExplanation(
        assessment_id=assessment_id, why_eligible=(), why_ineligible=(),
        supporting_evidence=("test",), weakening_evidence=(), what_would_change_it=("test",),
        schema_version=sei_taxonomy.SEI_VERSION,
    )
    return StrategyEligibilityAssessment(
        assessment_id=assessment_id, timestamp="2026-07-25T09:00:00",
        eligible_strategy_families=tuple(sorted(eligible_strategy_families)), ineligible_strategy_families=ineligible,
        eligibility_confidence=eligibility_confidence, supporting_assessment_ids=("dse-1", "mdci-1"),
        contradictions=(), explanation=explanation,
        provenance="msi_strategy_eligibility.engine.determine_eligibility", schema_version=sei_taxonomy.SEI_VERSION,
    )


# ---------------------------------------------------------------------------
# Test A -- a real Selection result reaches TradeIntent.
# ---------------------------------------------------------------------------
def test_real_selection_reaches_trade_intent_when_permitted():
    eligibility = _mk_eligibility({sei_taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL})
    opportunity = _mk_opportunity()
    intent = determine_trade_intent_from_selection(
        "LONG_DIRECTIONAL", "MODERATE", eligibility, opportunity, timestamp="2026-07-25T09:00:00",
    )
    assert intent is not None
    assert intent.selected_strategy_family == "LONG_DIRECTIONAL"
    assert intent.intent_state == ti_taxonomy.INTENT_STATE_FORMED


# ---------------------------------------------------------------------------
# Test B -- TradeIntent no longer independently chooses a different
# family when modern Selection output is supplied.
# ---------------------------------------------------------------------------
def test_never_substitutes_a_different_family_than_selection_chose():
    """Even though HEDGED_DIRECTIONAL (eligible) would let the OLD
    placeholder pick RATIO or SYNTHETIC, the modern path must return
    exactly what Selection picked (SYNTHETIC), never substitute RATIO."""
    eligibility = _mk_eligibility({sei_taxonomy.FAMILY_HEDGED_DIRECTIONAL})
    opportunity = _mk_opportunity()
    intent = determine_trade_intent_from_selection(
        "SYNTHETIC", "LOW", eligibility, opportunity, timestamp="2026-07-25T09:00:00",
    )
    assert intent is not None
    assert intent.selected_strategy_family == "SYNTHETIC"


def test_selection_pick_not_eligible_returns_none_never_forced():
    """Selection picked something Eligibility does not permit -- must
    fail closed, never fabricate an intent."""
    eligibility = _mk_eligibility({sei_taxonomy.FAMILY_LONG_VOLATILITY})
    opportunity = _mk_opportunity()
    intent = determine_trade_intent_from_selection(
        "LONG_DIRECTIONAL", "HIGH", eligibility, opportunity, timestamp="2026-07-25T09:00:00",
    )
    assert intent is None


def test_no_selected_family_returns_none():
    eligibility = _mk_eligibility({sei_taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL})
    opportunity = _mk_opportunity()
    intent = determine_trade_intent_from_selection(None, None, eligibility, opportunity, timestamp="t")
    assert intent is None


def test_eligibility_confidence_none_returns_none_never_forced():
    """Test F-equivalent at the TradeIntent layer: NONE eligibility
    confidence must never be treated as a green light."""
    eligibility = _mk_eligibility({sei_taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL}, eligibility_confidence=sei_taxonomy.ELIGIBILITY_CONFIDENCE_NONE)
    opportunity = _mk_opportunity()
    intent = determine_trade_intent_from_selection(
        "LONG_DIRECTIONAL", "MODERATE", eligibility, opportunity, timestamp="t",
    )
    assert intent is None


def test_missing_eligibility_or_opportunity_returns_none():
    eligibility = _mk_eligibility({sei_taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL})
    assert determine_trade_intent_from_selection("LONG_DIRECTIONAL", "HIGH", None, _mk_opportunity(), timestamp="t") is None
    assert determine_trade_intent_from_selection("LONG_DIRECTIONAL", "HIGH", eligibility, None, timestamp="t") is None


# ---------------------------------------------------------------------------
# Test G -- legacy callers of TradeIntent still behave correctly.
# ---------------------------------------------------------------------------
def test_legacy_determine_trade_intent_unaffected_by_new_function():
    eligibility = _mk_eligibility({sei_taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL, sei_taxonomy.FAMILY_HEDGED_DIRECTIONAL})
    opportunity = _mk_opportunity()
    legacy_intent = determine_trade_intent(eligibility, opportunity, timestamp="2026-07-25T09:00:00")
    assert legacy_intent is not None
    # legacy placeholder still uses ITS OWN alphabetical-priority selection --
    # unrelated to, and unaffected by, the new function existing.
    assert legacy_intent.selected_strategy_family in eligibility.eligible_strategy_families


# ---------------------------------------------------------------------------
# Test J -- no execution capability introduced.
# ---------------------------------------------------------------------------
def test_new_function_never_imports_broker_or_execution():
    import inspect
    from bujji.msi_trade_intent import engine as ti_engine
    source = inspect.getsource(ti_engine)
    for forbidden in ("place_order", "modify_order", "cancel_order", "bujji.broker", "execution_engine"):
        assert forbidden not in source


def test_result_json_serializable():
    import dataclasses
    import json
    eligibility = _mk_eligibility({sei_taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL})
    opportunity = _mk_opportunity()
    intent = determine_trade_intent_from_selection(
        "LONG_DIRECTIONAL", "HIGH", eligibility, opportunity, timestamp="t",
    )
    json.dumps(dataclasses.asdict(intent))
