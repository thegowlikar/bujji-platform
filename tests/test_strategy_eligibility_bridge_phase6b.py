"""Tests -- Strategy Eligibility Bridge, Shadow Trading Brain Phase 6B.
No broker, no network -- directly constructed real assessment
dataclasses only."""
from __future__ import annotations

from bujji.msi_consensus.models import Explanation as ConsensusExplanation
from bujji.msi_consensus.models import ConsensusAssessment
from bujji.msi_decision_synthesis.models import Explanation as OpportunityExplanation
from bujji.msi_decision_synthesis.models import MarketOpportunityAssessment
from bujji.msi_strategy_eligibility.models import StrategyEligibilityAssessment
from bujji.market_state.strategy_eligibility_bridge import build_strategy_eligibility

TS = "2026-08-03T09:15:00+05:30"


def _build_opportunity(opportunity_state, confidence_level):
    # MarketOpportunityAssessment itself has no explanation field --
    # Explanation is a separate, sibling dataclass in the same module,
    # not embedded here (confirmed by construction, not assumed).
    return MarketOpportunityAssessment(
        assessment_id="MOA-1", timestamp=TS, opportunity_state=opportunity_state,
        confidence_level=confidence_level, opportunity_quality="GOOD",
        supporting_domains=("PRICE_STRUCTURE",), conflicting_domains=(),
        compatible_strategy_families=(), incompatible_strategy_families=(),
        evidence_ids=("OBS-1",), episode_ids=("EP-1",),
        provenance="test", schema_version="1.0",
    )


def make_consensus(consensus_level="STRONG_CONSENSUS", evidence_sufficiency="ADEQUATE"):
    explanation = ConsensusExplanation(
        assessment_id="CA-1", which_domains_agree=("PRICE_STRUCTURE",), which_domains_disagree=(),
        which_evidence_is_missing=(), why_consensus_is_high_or_low="test",
        what_additional_domains_would_increase_confidence=(), what_changed=None, schema_version="1.0",
    )
    return ConsensusAssessment(
        assessment_id="CA-1", timestamp=TS, participating_domains=("PRICE_STRUCTURE",),
        agreeing_domains=("PRICE_STRUCTURE",), conflicting_domains=(), missing_domains=(),
        consensus_level=consensus_level, evidence_sufficiency=evidence_sufficiency,
        contradiction_density=0.0, confidence_calibration="HIGH",
        supporting_assessment_ids=("PSI-1",), explanation=explanation,
        provenance="test", schema_version="1.0",
    )


# --- Functional tests ---

def test_valid_opportunity_and_consensus_produce_assessment():
    opportunity, consensus = _build_opportunity("DIRECTIONAL_OPPORTUNITY", "HIGH"), make_consensus()
    result = build_strategy_eligibility(opportunity, consensus, TS)
    assert result is not None
    assert isinstance(result, StrategyEligibilityAssessment)


def test_directional_opportunity_preserves_multiple_eligible_families():
    opportunity, consensus = _build_opportunity("DIRECTIONAL_OPPORTUNITY", "HIGH"), make_consensus()
    result = build_strategy_eligibility(opportunity, consensus, TS)
    assert len(result.eligible_strategy_families) > 1  # a SET, never narrowed to one
    assert "DEFINED_RISK_DIRECTIONAL" in result.eligible_strategy_families
    assert "HEDGED_DIRECTIONAL" in result.eligible_strategy_families
    assert "DIAGONAL" in result.eligible_strategy_families


def test_breakout_opportunity_correct_family_compatibility():
    opportunity, consensus = _build_opportunity("BREAKOUT_OPPORTUNITY", "HIGH"), make_consensus()
    result = build_strategy_eligibility(opportunity, consensus, TS)
    assert set(result.eligible_strategy_families) == {
        "DEFINED_RISK_DIRECTIONAL", "HEDGED_DIRECTIONAL", "LONG_VOLATILITY",
    }


def test_no_action_wait_monitor_produce_empty_eligible_families():
    consensus = make_consensus()
    for state in ("NO_ACTION", "WAIT", "MONITOR"):
        opportunity = _build_opportunity(state, "NONE")
        result = build_strategy_eligibility(opportunity, consensus, TS)
        assert result.eligible_strategy_families == (), f"{state} should have zero eligible families"


def test_weak_consensus_contradiction_preserved_never_resolved():
    # A confident directional read sitting on top of weak/insufficient
    # consensus is exactly the tension SEI is designed to surface.
    opportunity = _build_opportunity("DIRECTIONAL_OPPORTUNITY", "HIGH")
    consensus = make_consensus(consensus_level="WEAK_CONSENSUS", evidence_sufficiency="INSUFFICIENT")
    result = build_strategy_eligibility(opportunity, consensus, TS)
    assert result is not None
    # The tension is surfaced somewhere (contradictions, weakened
    # confidence, or explanation's weakening_evidence) -- never silently
    # dropped or resolved into a confident answer despite weak backing.
    assert (
        result.contradictions != ()
        or result.eligibility_confidence in ("NONE", "LOW")
        or result.explanation.weakening_evidence != ()
    )


def test_missing_inputs_handled_safely_at_bridge_level():
    opportunity, consensus = _build_opportunity("DIRECTIONAL_OPPORTUNITY", "HIGH"), make_consensus()
    assert build_strategy_eligibility(None, consensus, TS) is None
    assert build_strategy_eligibility(opportunity, None, TS) is None
    assert build_strategy_eligibility(None, None, TS) is None


# --- Boundary tests ---

def test_bridge_never_reduces_eligible_set_to_a_single_selection():
    opportunity, consensus = _build_opportunity("DIRECTIONAL_OPPORTUNITY", "HIGH"), make_consensus()
    result = build_strategy_eligibility(opportunity, consensus, TS)
    # The bridge and its output type expose no "selected_strategy" or
    # similarly-singular field anywhere -- eligible_strategy_families
    # is a tuple/set, structurally incapable of collapsing to one.
    assert isinstance(result.eligible_strategy_families, tuple)
    assert not hasattr(result, "selected_strategy")
    assert not hasattr(result, "winning_strategy")
    assert not hasattr(result, "preferred_strategy")


def test_no_execution_shaped_fields_on_eligibility_assessment():
    field_names = set(StrategyEligibilityAssessment.__dataclass_fields__.keys())
    forbidden = {
        "entry", "exit", "strike", "quantity", "order", "capital",
        "risk_approval", "position", "selected_strategy",
    }
    assert not (field_names & forbidden), f"forbidden fields found: {field_names & forbidden}"
