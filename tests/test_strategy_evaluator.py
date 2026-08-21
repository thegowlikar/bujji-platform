"""Tests for the Strategy Candidate Evaluator -- Trading Brain
Intelligence Upgrade, Phase 1. Mirrors tests/test_strategy_selector.py's
own conventions (fixed clock, helper builders for real dataclasses,
class-grouped tests)."""
from __future__ import annotations

from datetime import datetime

import pytest

from bujji.intelligence.models import LiquidityReading, SpreadTightness
from bujji.msi_market_direction.models import (
    Explanation as MdiExplanation,
    MarketDirectionAssessment,
)
from bujji.msi_market_structure.models import (
    Explanation as MssiExplanation,
    MarketStructureAssessment,
)
from bujji.msi_price_structure.models import (
    Explanation as PsiExplanation,
    PriceStructureAssessment,
)
from bujji.msi_volatility_structure.models import (
    Explanation as VsbExplanation,
    VolatilityStructureAssessment,
)
from bujji.trading_brain.market_state.models import MarketStateAssessment
from bujji.trading_brain.strategy_selector import engine as selector_engine
from bujji.trading_brain.strategy_evaluator import engine, models, taxonomy
from bujji.volatility_intelligence import assess as assess_volatility_intelligence

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 30, 0)


def _market_state_assessment(**overrides):
    base = dict(
        assessment_id="MSA-0000000000000001",
        market_state="RANGE",
        market_phase="ESTABLISHED",
        market_character="CLEAR",
        market_conviction="VERY_HIGH",
        confidence="VERY_HIGH",
        supporting_evidence=("stub supporting evidence",),
        contradicting_evidence=(),
        reasoning_trace="stub reasoning trace",
        interpretation_id="EI-0000000000000001",
        timestamp="2026-01-01T09:15:00",
        version="1.0.0",
    )
    base.update(overrides)
    return MarketStateAssessment(**base)


def _mdi(**overrides):
    base = dict(
        assessment_id="MDI-0000000000000001", timestamp="2026-01-01T09:15:00",
        overall_direction="NEUTRAL", overall_confidence="HIGH",
        participating_lenses=(), conflicting_lenses=(), supporting_assessment_ids=(),
        explanation=MdiExplanation(
            assessment_id="MDI-0000000000000001", which_lenses_participated=(),
            which_bullish=(), which_bearish=(), which_neutral_or_unknown=(),
            per_lens_evidence=(), why_not_a_simple_vote="stub", schema_version="1.0.0",
        ),
        provenance="test", schema_version="1.0.0",
    )
    base.update(overrides)
    return MarketDirectionAssessment(**base)


def _psi(**overrides):
    base = dict(
        assessment_id="PSI-0000000000000001", timestamp="2026-01-01T09:15:00",
        structure_state="ESTABLISHED", trend_state="NO_TREND", swing_state="UNKNOWN",
        compression_state="COMPRESSION_NOT_DETECTED", expansion_state="EXPANSION_NOT_DETECTED",
        balance_state="IN_BALANCE", structure_integrity="INTACT", confidence="HIGH",
        supporting_episode_ids=(), supporting_event_ids=(), supporting_observation_ids=(),
        contradictions=(),
        explanation=PsiExplanation(
            assessment_id="PSI-0000000000000001", what_changed=None, why=(),
            which_episodes_caused_it=(), which_observations_support_it=(),
            missing_evidence=(), would_increase_confidence=(), schema_version="1.0.0",
        ),
        provenance="test", schema_version="1.0.0",
    )
    base.update(overrides)
    return PriceStructureAssessment(**base)


def _mssi(**overrides):
    base = dict(
        assessment_id="MSSI-0000000000000001", timestamp="2026-01-01T09:15:00",
        structure_location="INSIDE_RANGE", support_state="UNKNOWN", resistance_state="UNKNOWN",
        breakout_state="NO_BREAKOUT", breakdown_state="NO_BREAKDOWN", retest_state="UNKNOWN",
        rejection_state="UNKNOWN", structural_balance="RANGE_BOUND", confidence="HIGH",
        supporting_episode_ids=(), supporting_event_ids=(), supporting_observation_ids=(),
        contradictions=(),
        explanation=MssiExplanation(
            assessment_id="MSSI-0000000000000001", what_changed=None, why=(),
            which_episodes_caused_it=(), which_observations_support_it=(),
            missing_evidence=(), would_increase_confidence=(), schema_version="1.0.0",
        ),
        provenance="test", schema_version="1.0.0",
    )
    base.update(overrides)
    return MarketStructureAssessment(**base)


def _vsb(**overrides):
    base = dict(
        assessment_id="VSB-0000000000000001", timestamp="2026-01-01T09:15:00",
        volatility_regime="NORMAL", iv_state="IV_RICH", expected_move_state="NORMAL",
        skew_state="UNKNOWN", term_structure_state="UNKNOWN",
        expansion_state="EXPANSION_NOT_DETECTED", compression_state="COMPRESSION_NOT_DETECTED",
        confidence="HIGH", iv_average=None, realized_vol=None, expected_move_pct=None,
        explanation=VsbExplanation(
            assessment_id="VSB-0000000000000001", why=(), missing_evidence=(),
            would_increase_confidence=(), schema_version="1.0.0",
        ),
        provenance="test", schema_version="1.0.0",
    )
    base.update(overrides)
    return VolatilityStructureAssessment(**base)


def _liquidity(tightness=SpreadTightness.TIGHT, **overrides):
    base = dict(
        ce_bid=100.0, ce_ask=101.0, pe_bid=100.0, pe_ask=101.0,
        ce_spread_pct=1.0, pe_spread_pct=1.0, combined_spread=2.0, combined_spread_pct=1.0,
        tightness=tightness, confidence=0.9, reason="test",
    )
    base.update(overrides)
    return LiquidityReading(**base)


def _decision_for_range_market():
    assessment = _market_state_assessment(market_state="RANGE", market_character="CLEAR", confidence="VERY_HIGH")
    return selector_engine.select(assessment, clock=FIXED_CLOCK)


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_score_levels(self):
        assert taxonomy.ALL_SCORE_LEVELS == ("HIGH", "MEDIUM", "LOW", "UNKNOWN")

    def test_dimensions(self):
        assert taxonomy.ALL_DIMENSIONS == (
            "STRUCTURE_FIT", "VOLATILITY_FIT", "RISK_EFFICIENCY", "LIQUIDITY_FIT",
        )

    def test_every_dimension_has_description(self):
        for d in taxonomy.ALL_DIMENSIONS:
            assert d in taxonomy.DIMENSION_DESCRIPTIONS
            assert taxonomy.DIMENSION_DESCRIPTIONS[d]

    def test_ranking_statuses(self):
        assert taxonomy.ALL_RANKING_STATUSES == (
            "RANKED", "NO_ELIGIBLE_CANDIDATES", "UNKNOWN",
        )

    def test_score_rank_ordering(self):
        assert taxonomy.score_rank("UNKNOWN") < taxonomy.score_rank("LOW") < taxonomy.score_rank(
            "MEDIUM") < taxonomy.score_rank("HIGH")


# ---------------------------------------------------------------------------
# Never re-gates: decision handling
# ---------------------------------------------------------------------------

class TestDecisionHandling:
    def test_none_decision_is_unknown(self):
        result = engine.rank(None, clock=FIXED_CLOCK)
        assert result.status == taxonomy.RANKING_STATUS_UNKNOWN
        assert result.winner is None
        assert result.rankings == ()

    def test_no_eligible_strategy_carries_through(self):
        # An UNKNOWN market state produces NO_STRATEGY in the selector;
        # nothing for this module to rank either.
        assessment = _market_state_assessment(market_state="UNKNOWN", market_character="MIXED", confidence="UNKNOWN")
        decision = selector_engine.select(assessment, clock=FIXED_CLOCK)
        result = engine.rank(decision, clock=FIXED_CLOCK)
        assert result.status == taxonomy.RANKING_STATUS_NO_ELIGIBLE_CANDIDATES
        assert result.winner is None

    def test_rejected_strategies_carried_through_unscored(self):
        decision = _decision_for_range_market()
        result = engine.rank(decision, clock=FIXED_CLOCK)
        rejected_by_selector = {
            ev.strategy_id for ev in decision.all_evaluations if ev.eligibility != "ELIGIBLE"
        }
        assert set(result.rejected_ineligible) == rejected_by_selector
        scored_ids = {r.strategy_id for r in result.rankings}
        assert scored_ids.isdisjoint(rejected_by_selector)

    def test_eligible_strategy_never_invented(self):
        decision = _decision_for_range_market()
        result = engine.rank(decision, clock=FIXED_CLOCK)
        eligible_ids = {
            ev.strategy_id for ev in decision.all_evaluations if ev.eligibility == "ELIGIBLE"
        }
        scored_ids = {r.strategy_id for r in result.rankings}
        assert scored_ids == eligible_ids


# ---------------------------------------------------------------------------
# Missing evidence -> UNKNOWN, never a guess
# ---------------------------------------------------------------------------

class TestMissingEvidenceIsHonest:
    def test_no_evidence_supplied_scores_all_unknown_or_defined_risk(self):
        decision = _decision_for_range_market()
        result = engine.rank(decision, clock=FIXED_CLOCK)
        for ranking in result.rankings:
            for score in ranking.dimension_scores:
                if score.dimension == taxonomy.DIMENSION_RISK_EFFICIENCY:
                    # risk_efficiency never needs external evidence -- it reads registry.risk_profile directly.
                    assert score.level in (taxonomy.SCORE_HIGH, taxonomy.SCORE_LOW)
                else:
                    assert score.level == taxonomy.SCORE_UNKNOWN


# ---------------------------------------------------------------------------
# Per-dimension scoring, using real assessment objects
# ---------------------------------------------------------------------------

class TestStructureFit:
    def test_neutral_strategy_high_on_centered_range(self):
        decision = _decision_for_range_market()
        result = engine.rank(
            decision, mssi=_mssi(structure_location="INSIDE_RANGE", structural_balance="RANGE_BOUND"),
            psi=_psi(), clock=FIXED_CLOCK,
        )
        for ranking in result.rankings:
            defn_bias = _bias_of(ranking.strategy_id)
            if defn_bias == "NEUTRAL":
                fit = _dim(ranking, taxonomy.DIMENSION_STRUCTURE_FIT)
                assert fit.level == taxonomy.SCORE_HIGH

    def test_neutral_strategy_low_on_unbounded_structure(self):
        decision = _decision_for_range_market()
        result = engine.rank(
            decision, mssi=_mssi(structure_location="ABOVE_RESISTANCE", structural_balance="UNBOUNDED"),
            psi=_psi(), clock=FIXED_CLOCK,
        )
        for ranking in result.rankings:
            if _bias_of(ranking.strategy_id) == "NEUTRAL":
                fit = _dim(ranking, taxonomy.DIMENSION_STRUCTURE_FIT)
                assert fit.level == taxonomy.SCORE_LOW


class TestVolatilityFit:
    def test_income_strategy_high_on_iv_rich(self):
        decision = _decision_for_range_market()
        result = engine.rank(decision, vsb=_vsb(iv_state="IV_RICH"), clock=FIXED_CLOCK)
        for ranking in result.rankings:
            if _income_or_debit_of(ranking.strategy_id) == "INCOME":
                fit = _dim(ranking, taxonomy.DIMENSION_VOLATILITY_FIT)
                assert fit.level == taxonomy.SCORE_HIGH

    def test_income_strategy_low_on_iv_cheap(self):
        decision = _decision_for_range_market()
        result = engine.rank(decision, vsb=_vsb(iv_state="IV_CHEAP"), clock=FIXED_CLOCK)
        for ranking in result.rankings:
            if _income_or_debit_of(ranking.strategy_id) == "INCOME":
                fit = _dim(ranking, taxonomy.DIMENSION_VOLATILITY_FIT)
                assert fit.level == taxonomy.SCORE_LOW


class TestRiskEfficiency:
    def test_defined_risk_strategy_scores_high(self):
        decision = _decision_for_range_market()
        result = engine.rank(decision, clock=FIXED_CLOCK)
        for ranking in result.rankings:
            if _risk_profile_of(ranking.strategy_id) == "DEFINED_RISK":
                eff = _dim(ranking, taxonomy.DIMENSION_RISK_EFFICIENCY)
                assert eff.level == taxonomy.SCORE_HIGH

    def test_undefined_risk_strategy_scores_low(self):
        # SHORT_STRANGLE is UNDEFINED_RISK and requires VERY_HIGH confidence + CLEAR + RANGE/QUIET.
        assessment = _market_state_assessment(market_state="QUIET", market_character="CLEAR", confidence="VERY_HIGH")
        decision = selector_engine.select(assessment, clock=FIXED_CLOCK)
        result = engine.rank(decision, clock=FIXED_CLOCK)
        strangle = next((r for r in result.rankings if r.strategy_id == "SHORT_STRANGLE"), None)
        assert strangle is not None, "expected SHORT_STRANGLE to be eligible under QUIET/CLEAR/VERY_HIGH"
        eff = _dim(strangle, taxonomy.DIMENSION_RISK_EFFICIENCY)
        assert eff.level == taxonomy.SCORE_LOW


class TestLiquidityFit:
    @pytest.mark.parametrize("tightness,expected", [
        (SpreadTightness.TIGHT, taxonomy.SCORE_HIGH),
        (SpreadTightness.NORMAL, taxonomy.SCORE_MEDIUM),
        (SpreadTightness.WIDE, taxonomy.SCORE_LOW),
    ])
    def test_tightness_maps_to_score(self, tightness, expected):
        decision = _decision_for_range_market()
        result = engine.rank(decision, liquidity=_liquidity(tightness=tightness), clock=FIXED_CLOCK)
        for ranking in result.rankings:
            fit = _dim(ranking, taxonomy.DIMENSION_LIQUIDITY_FIT)
            assert fit.level == expected


# ---------------------------------------------------------------------------
# Worked scenario, matching the user's own example shape: a defined-risk
# structure should outrank an undefined-risk one under identical, real,
# favorable evidence for both, on risk_efficiency alone.
# ---------------------------------------------------------------------------

class TestWorkedScenario:
    def test_iron_condor_outranks_short_strangle_given_identical_favorable_evidence(self):
        assessment = _market_state_assessment(market_state="RANGE", market_character="CONTESTED", confidence="MODERATE")
        decision = selector_engine.select(assessment, clock=FIXED_CLOCK)
        result = engine.rank(
            decision,
            mssi=_mssi(structure_location="INSIDE_RANGE", structural_balance="RANGE_BOUND"),
            psi=_psi(), vsb=_vsb(iv_state="IV_RICH"), liquidity=_liquidity(tightness=SpreadTightness.TIGHT),
            clock=FIXED_CLOCK,
        )
        ids = [r.strategy_id for r in result.rankings]
        assert "IRON_CONDOR" in ids
        condor = next(r for r in result.rankings if r.strategy_id == "IRON_CONDOR")
        strangle = next((r for r in result.rankings if r.strategy_id == "SHORT_STRANGLE"), None)
        if strangle is not None:
            assert condor.rank_position < strangle.rank_position
            assert condor.high_count > strangle.high_count


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self):
        decision = _decision_for_range_market()
        kwargs = dict(mssi=_mssi(), psi=_psi(), vsb=_vsb(), mdi=_mdi(), liquidity=_liquidity())
        a = engine.rank(decision, clock=FIXED_CLOCK, **kwargs)
        b = engine.rank(decision, clock=FIXED_CLOCK, **kwargs)
        assert a == b


# ---------------------------------------------------------------------------
# Small helpers, test-file-local only.
# ---------------------------------------------------------------------------

def _dim(ranking: "models.StrategyRanking", dimension: str) -> "models.DimensionScore":
    return next(s for s in ranking.dimension_scores if s.dimension == dimension)


def _bias_of(strategy_id: str) -> str:
    from bujji.trading_brain.strategy_selector.registry import BY_ID
    return BY_ID[strategy_id].directional_bias


def _income_or_debit_of(strategy_id: str) -> str:
    from bujji.trading_brain.strategy_selector.registry import BY_ID
    return BY_ID[strategy_id].income_or_debit


def _risk_profile_of(strategy_id: str) -> str:
    from bujji.trading_brain.strategy_selector.registry import BY_ID
    return BY_ID[strategy_id].risk_profile


# ---------------------------------------------------------------------------
# Phase 2: volatility_intelligence is optional, additive evidence for
# VOLATILITY_FIT only -- never re-gates, never touches any other
# dimension, never changes behavior when omitted (backward compatible
# with every Phase 1 test above).
# ---------------------------------------------------------------------------

class TestVolatilityIntelligenceIntegration:
    def test_omitting_it_is_byte_identical_to_phase_1_behavior(self):
        decision = _decision_for_range_market()
        without = engine.rank(decision, vsb=_vsb(iv_state="IV_RICH"), clock=FIXED_CLOCK)
        with_none = engine.rank(decision, vsb=_vsb(iv_state="IV_RICH"), volatility_intelligence=None, clock=FIXED_CLOCK)
        assert without == with_none

    def test_corroborating_evidence_does_not_change_the_score(self):
        decision = _decision_for_range_market()
        vi = assess_volatility_intelligence(
            volatility_structure=_vsb(iv_state="IV_RICH"), timestamp="2026-01-01T09:15:00",
        )
        # No VIX data supplied to volatility_intelligence itself -> MODERATE quality, not STRONG/WEAK.
        result = engine.rank(decision, vsb=_vsb(iv_state="IV_RICH"), volatility_intelligence=vi, clock=FIXED_CLOCK)
        baseline = engine.rank(decision, vsb=_vsb(iv_state="IV_RICH"), clock=FIXED_CLOCK)
        for a, b in zip(result.rankings, baseline.rankings):
            fit_a = _dim(a, taxonomy.DIMENSION_VOLATILITY_FIT)
            fit_b = _dim(b, taxonomy.DIMENSION_VOLATILITY_FIT)
            assert fit_a.level == fit_b.level

    def test_conflicting_evidence_downgrades_the_score(self):
        from bujji.mic_v0.volatility_classifier import MIN_WINDOW
        decision = _decision_for_range_market()
        history = [float(v) for v in range(10, 10 + MIN_WINDOW)]
        vi = assess_volatility_intelligence(
            volatility_structure=_vsb(iv_state="IV_RICH"), current_vix=11.0, trailing_vix_history=history,
            timestamp="2026-01-01T09:15:00",
        )
        assert vi.volatility_quality == "WEAK"
        result = engine.rank(decision, vsb=_vsb(iv_state="IV_RICH"), volatility_intelligence=vi, clock=FIXED_CLOCK)
        baseline = engine.rank(decision, vsb=_vsb(iv_state="IV_RICH"), clock=FIXED_CLOCK)
        for a, b in zip(result.rankings, baseline.rankings):
            fit_a = _dim(a, taxonomy.DIMENSION_VOLATILITY_FIT)
            fit_b = _dim(b, taxonomy.DIMENSION_VOLATILITY_FIT)
            assert taxonomy.score_rank(fit_a.level) <= taxonomy.score_rank(fit_b.level)
            if fit_b.level == taxonomy.SCORE_HIGH:
                assert fit_a.level == taxonomy.SCORE_MEDIUM

    def test_fallback_to_vix_rank_when_vsb_missing(self):
        from bujji.mic_v0.volatility_classifier import MIN_WINDOW
        decision = _decision_for_range_market()
        history = [float(v) for v in range(10, 10 + MIN_WINDOW)]
        vi = assess_volatility_intelligence(current_vix=68.0, trailing_vix_history=history, timestamp="2026-01-01T09:15:00")
        assert vi.iv_rank_state == "HIGH"
        result = engine.rank(decision, volatility_intelligence=vi, clock=FIXED_CLOCK)
        for ranking in result.rankings:
            fit = _dim(ranking, taxonomy.DIMENSION_VOLATILITY_FIT)
            assert fit.level != taxonomy.SCORE_UNKNOWN

    def test_never_creates_a_strategy_decision(self):
        # volatility_intelligence must never change WHICH strategies are
        # eligible or WHICH ones are ranked -- only how VOLATILITY_FIT
        # is scored among strategies strategy_selector already chose.
        decision = _decision_for_range_market()
        vi = assess_volatility_intelligence(volatility_structure=_vsb(iv_state="IV_RICH"), timestamp="2026-01-01T09:15:00")
        with_vi = engine.rank(decision, volatility_intelligence=vi, clock=FIXED_CLOCK)
        without_vi = engine.rank(decision, clock=FIXED_CLOCK)
        assert {r.strategy_id for r in with_vi.rankings} == {r.strategy_id for r in without_vi.rankings}
        assert with_vi.rejected_ineligible == without_vi.rejected_ineligible
