"""Tests for the Intelligence Decision Orchestrator -- Trading Brain
Intelligence Upgrade, Phase 4. A thin conductor over market_thesis,
strategy_selector, and strategy_evaluator -- these tests verify
composition, honest skipping of missing stages, and the two small new
pieces of logic (TRADE/NO_TRADE + confidence ladder, MSI cross-
reference), never re-test the real engines' own internal rules."""
from __future__ import annotations

from datetime import datetime

import pytest

from bujji.construction_shape_bridge.bridge import STRUCTURE_TO_FAMILY
from bujji.intelligence.models import LiquidityReading, SpreadTightness
from bujji.intelligence_orchestrator import (
    DecisionContext, evaluate_strategies, generate_thesis, orchestrate, taxonomy,
)
from bujji.intelligence_orchestrator.engine import _confidence_from_high_count, _msi_cross_reference
from bujji.msi_consensus.models import Explanation as ConsensusExplanation, ConsensusAssessment
from bujji.msi_consensus import taxonomy as consensus_taxonomy
from bujji.msi_market_direction.models import Explanation as MdiExplanation, MarketDirectionAssessment
from bujji.msi_market_structure.models import Explanation as MssiExplanation, MarketStructureAssessment
from bujji.msi_participant_positioning.models import (
    Explanation as MppiExplanation, MarketParticipantPositioningAssessment,
)
from bujji.msi_price_structure.models import Explanation as PsiExplanation, PriceStructureAssessment
from bujji.msi_volatility_structure.models import Explanation as VsbExplanation, VolatilityStructureAssessment
from bujji.trading_brain.market_state.models import MarketStateAssessment

TS = "2026-01-01T09:15:00"
FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 30, 0)


def _psi(**overrides):
    base = dict(
        assessment_id="PSI-1", timestamp=TS, structure_state="BALANCE", trend_state="NO_TREND",
        swing_state="UNKNOWN", compression_state="NOT_DETECTED", expansion_state="NOT_DETECTED",
        balance_state="IN_BALANCE", structure_integrity="COHERENT", confidence="HIGH",
        supporting_episode_ids=(), supporting_event_ids=(), supporting_observation_ids=(), contradictions=(),
        explanation=PsiExplanation(assessment_id="PSI-1", what_changed=None, why=(), which_episodes_caused_it=(),
                                    which_observations_support_it=(), missing_evidence=(), would_increase_confidence=(), schema_version="1.0.0"),
        provenance="test", schema_version="1.0.0",
    )
    base.update(overrides)
    return PriceStructureAssessment(**base)


def _mssi(**overrides):
    base = dict(
        assessment_id="MSSI-1", timestamp=TS, structure_location="INSIDE_RANGE", support_state="UNKNOWN",
        resistance_state="UNKNOWN", breakout_state="NONE", breakdown_state="NONE", retest_state="UNKNOWN",
        rejection_state="UNKNOWN", structural_balance="RANGE_BOUND", confidence="HIGH",
        supporting_episode_ids=(), supporting_event_ids=(), supporting_observation_ids=(), contradictions=(),
        explanation=MssiExplanation(assessment_id="MSSI-1", what_changed=None, why=(), which_episodes_caused_it=(),
                                     which_observations_support_it=(), missing_evidence=(), would_increase_confidence=(), schema_version="1.0.0"),
        provenance="test", schema_version="1.0.0",
    )
    base.update(overrides)
    return MarketStructureAssessment(**base)


def _mdi(**overrides):
    base = dict(
        assessment_id="MDI-1", timestamp=TS, overall_direction="NEUTRAL", overall_confidence="HIGH",
        participating_lenses=(), conflicting_lenses=(), supporting_assessment_ids=(),
        explanation=MdiExplanation(assessment_id="MDI-1", which_lenses_participated=(), which_bullish=(),
                                    which_bearish=(), which_neutral_or_unknown=(), per_lens_evidence=(),
                                    why_not_a_simple_vote="stub", schema_version="1.0.0"),
        provenance="test", schema_version="1.0.0",
    )
    base.update(overrides)
    return MarketDirectionAssessment(**base)


def _mppi(**overrides):
    base = dict(
        assessment_id="MPPI-1", timestamp=TS, positioning_bias="NEUTRAL_POSITIONING", positioning_strength="MODERATE",
        participating_lenses=(), conflicting_lenses=(), supporting_observation_ids=(),
        explanation=MppiExplanation(assessment_id="MPPI-1", which_lenses_participated=(), which_bullish=(),
                                     which_bearish=(), which_neutral_or_unknown=(), per_lens_evidence=(),
                                     missing_evidence=(), why_positioning_was_chosen="stub", schema_version="1.0.0"),
        provenance="test", schema_version="1.0.0",
    )
    base.update(overrides)
    return MarketParticipantPositioningAssessment(**base)


def _vsb(**overrides):
    base = dict(
        assessment_id="VSB-1", timestamp=TS, volatility_regime="STABLE", iv_state="IV_RICH",
        expected_move_state="MODERATE", skew_state="UNKNOWN", term_structure_state="UNKNOWN",
        expansion_state="NOT_DETECTED", compression_state="NOT_DETECTED", confidence="HIGH",
        iv_average=0.18, realized_vol=0.12, expected_move_pct=1.2,
        explanation=VsbExplanation(assessment_id="VSB-1", why=(), missing_evidence=(), would_increase_confidence=(), schema_version="1.0.0"),
        provenance="test", schema_version="1.0.0",
    )
    base.update(overrides)
    return VolatilityStructureAssessment(**base)


def _consensus(**overrides):
    base = dict(
        assessment_id="CON-1", timestamp=TS, participating_domains=(), agreeing_domains=(), conflicting_domains=(),
        missing_domains=(), consensus_level=consensus_taxonomy.CONSENSUS_MODERATE,
        evidence_sufficiency=consensus_taxonomy.SUFFICIENCY_ADEQUATE,
        contradiction_density=0.0, confidence_calibration=consensus_taxonomy.CALIBRATION_WELL_CALIBRATED,
        supporting_assessment_ids=(),
        explanation=ConsensusExplanation(assessment_id="CON-1", which_domains_agree=(), which_domains_disagree=(),
                                          which_evidence_is_missing=(), why_consensus_is_high_or_low="stub",
                                          what_additional_domains_would_increase_confidence=(), what_changed=None, schema_version="1.0.0"),
        provenance="test", schema_version="1.0.0",
    )
    base.update(overrides)
    return ConsensusAssessment(**base)


def _liquidity(tightness=SpreadTightness.TIGHT, **overrides):
    base = dict(
        ce_bid=100.0, ce_ask=101.0, pe_bid=100.0, pe_ask=101.0, ce_spread_pct=1.0, pe_spread_pct=1.0,
        combined_spread=2.0, combined_spread_pct=1.0, tightness=tightness, confidence=0.9, reason="test",
    )
    base.update(overrides)
    return LiquidityReading(**base)


def _market_state_assessment(**overrides):
    base = dict(
        assessment_id="MSA-0000000000000001", market_state="RANGE", market_phase="ESTABLISHED",
        market_character="CLEAR", market_conviction="VERY_HIGH", confidence="VERY_HIGH",
        supporting_evidence=("stub",), contradicting_evidence=(), reasoning_trace="stub",
        interpretation_id="EI-1", timestamp=TS, version="1.0.0",
    )
    base.update(overrides)
    return MarketStateAssessment(**base)


def _full_context(**overrides):
    base = dict(
        timestamp=TS, psi=_psi(), mssi=_mssi(), mdi=_mdi(), mppi=_mppi(), vsb=_vsb(), consensus=_consensus(),
        liquidity=_liquidity(), market_state_assessment=_market_state_assessment(),
    )
    base.update(overrides)
    return DecisionContext(**base)


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_decision_statuses(self):
        assert taxonomy.ALL_DECISION_STATUSES == ("TRADE", "NO_TRADE")

    def test_confidence_levels(self):
        assert taxonomy.ALL_CONFIDENCE_LEVELS == ("NONE", "LOW", "MODERATE", "HIGH")


# ---------------------------------------------------------------------------
# Stage-by-stage composition
# ---------------------------------------------------------------------------

class TestAssessMarket:
    def test_passthrough_unchanged(self):
        from bujji.intelligence_orchestrator import assess_market
        context = _full_context()
        assert assess_market(context) is context


class TestGenerateThesis:
    def test_matches_direct_market_thesis_call(self):
        from bujji.market_thesis.engine import assess as direct_assess
        context = _full_context()
        via_orchestrator = generate_thesis(context)
        direct = direct_assess(
            psi=context.psi, mssi=context.mssi, mdi=context.mdi, mppi=context.mppi, vsb=context.vsb,
            consensus=context.consensus, liquidity=context.liquidity,
            volatility_intelligence=context.volatility_intelligence, timestamp=context.timestamp,
        )
        assert via_orchestrator == direct


class TestEvaluateStrategies:
    def test_skipped_when_no_market_state_assessment(self):
        context = _full_context(market_state_assessment=None)
        decision, ranked = evaluate_strategies(context)
        assert decision is None
        assert ranked is None

    def test_real_when_market_state_assessment_supplied(self):
        context = _full_context()
        decision, ranked = evaluate_strategies(context)
        assert decision is not None
        assert ranked is not None
        assert ranked.strategy_decision_id == decision.decision_id


# ---------------------------------------------------------------------------
# End-to-end orchestrate()
# ---------------------------------------------------------------------------

class TestOrchestrateEndToEnd:
    def test_trade_case_produces_full_trace(self):
        trace = orchestrate(_full_context())
        assert trace.outcome.decision_status in taxonomy.ALL_DECISION_STATUSES
        assert trace.market_thesis_assessment_id is not None
        assert trace.strategy_decision_id is not None
        assert trace.ranked_candidates_id is not None
        assert len(trace.steps) >= 5
        assert trace.provenance == "bujji.intelligence_orchestrator.engine.orchestrate"

    def test_no_trade_when_no_market_state_assessment(self):
        trace = orchestrate(_full_context(market_state_assessment=None))
        assert trace.outcome.decision_status == taxonomy.DECISION_NO_TRADE
        assert trace.outcome.selected_strategy is None
        assert trace.outcome.confidence == taxonomy.CONFIDENCE_NONE
        assert trace.strategy_decision_id is None
        assert trace.ranked_candidates_id is None
        assert any("skipped" in s for s in trace.steps)

    def test_no_trade_when_market_state_is_unknown(self):
        context = _full_context(market_state_assessment=_market_state_assessment(
            market_state="UNKNOWN", market_character="MIXED", confidence="UNKNOWN",
        ))
        trace = orchestrate(context)
        assert trace.outcome.decision_status == taxonomy.DECISION_NO_TRADE
        assert trace.outcome.selected_strategy is None

    def test_premium_behaviour_disclosed_but_unused(self):
        trace = orchestrate(_full_context(premium_behaviour=object()))
        assert any("premium_behaviour" in s and "not consumed" in s for s in trace.steps)

    def test_supporting_assessment_ids_populated(self):
        trace = orchestrate(_full_context())
        assert trace.market_thesis_assessment_id in trace.supporting_assessment_ids
        assert trace.strategy_decision_id in trace.supporting_assessment_ids
        assert trace.ranked_candidates_id in trace.supporting_assessment_ids


# ---------------------------------------------------------------------------
# MSI cross-reference -- the one open seam, disclosed not guessed.
# ---------------------------------------------------------------------------

class TestMsiCrossReference:
    def test_none_winner_no_cross_reference(self):
        from bujji.market_thesis.engine import assess as thesis_assess
        thesis = thesis_assess(psi=_psi(), mssi=_mssi(), mdi=_mdi(), mppi=_mppi(), timestamp=TS)
        msg = _msi_cross_reference(None, thesis)
        assert "No strategy was selected" in msg

    def test_mapped_strategy_id_references_real_family(self):
        from bujji.market_thesis.engine import assess as thesis_assess
        thesis = thesis_assess(
            psi=_psi(), mssi=_mssi(), mdi=_mdi(), mppi=_mppi(), vsb=_vsb(), consensus=_consensus(),
            liquidity=_liquidity(), timestamp=TS,
        )
        for strategy_id, family in STRUCTURE_TO_FAMILY.items():
            msg = _msi_cross_reference(strategy_id, thesis)
            assert family in msg
            assert "no verified MSI family mapping" not in msg

    def test_unmapped_strategy_id_is_honest(self):
        from bujji.market_thesis.engine import assess as thesis_assess
        thesis = thesis_assess(psi=_psi(), mssi=_mssi(), mdi=_mdi(), mppi=_mppi(), timestamp=TS)
        msg = _msi_cross_reference("PREMIUM_VWAP_STRADDLE", thesis)
        assert "No verified MSI family mapping" in msg

    def test_full_orchestrate_cross_reference_is_consistent(self):
        trace = orchestrate(_full_context())
        winner = trace.outcome.selected_strategy
        if winner in STRUCTURE_TO_FAMILY:
            assert STRUCTURE_TO_FAMILY[winner] in trace.outcome.msi_cross_reference
        elif winner is not None:
            assert "No verified MSI family mapping" in trace.outcome.msi_cross_reference


# ---------------------------------------------------------------------------
# Confidence ladder -- the other small new piece of logic.
# ---------------------------------------------------------------------------

class TestConfidenceLadder:
    @pytest.mark.parametrize("high_count,expected", [
        (0, "NONE"), (1, "LOW"), (2, "MODERATE"), (3, "HIGH"), (4, "HIGH"),
    ])
    def test_ladder(self, high_count, expected):
        assert _confidence_from_high_count(high_count) == expected


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self):
        context = _full_context()
        a = orchestrate(context, clock=FIXED_CLOCK)
        b = orchestrate(context, clock=FIXED_CLOCK)
        assert a == b
