"""Tests for Market Thesis -- Trading Brain Intelligence Upgrade,
Phase 3. A thin composition layer over msi_trade_thesis and
msi_strategy_selection_foundation -- these tests verify composition
and honesty (pass-through correctness, missing-evidence handling,
family-bucket partitioning), never re-test derive_trade_thesis's own
voting logic or SSF's own per-family rules (those are covered by their
own existing test suites)."""
from __future__ import annotations

import pytest

from bujji.intelligence.models import LiquidityReading, SpreadTightness
from bujji.msi_consensus.models import Explanation as ConsensusExplanation, ConsensusAssessment
from bujji.msi_consensus import taxonomy as consensus_taxonomy
from bujji.msi_market_direction.models import Explanation as MdiExplanation, MarketDirectionAssessment
from bujji.msi_market_structure.models import Explanation as MssiExplanation, MarketStructureAssessment
from bujji.msi_participant_positioning.models import (
    Explanation as MppiExplanation, MarketParticipantPositioningAssessment,
)
from bujji.msi_price_structure.models import Explanation as PsiExplanation, PriceStructureAssessment
from bujji.msi_strategy_selection_foundation import taxonomy as ssf_taxonomy
from bujji.msi_volatility_structure.models import Explanation as VsbExplanation, VolatilityStructureAssessment
from bujji.market_thesis import assess, taxonomy
from bujji.volatility_intelligence import assess as assess_volatility_intelligence

TS = "2026-01-01T09:15:00"


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


def _full_kwargs(**overrides):
    base = dict(psi=_psi(), mssi=_mssi(), mdi=_mdi(), mppi=_mppi(), vsb=_vsb(), consensus=_consensus())
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_premium_environments(self):
        assert taxonomy.ALL_PREMIUM_ENVIRONMENTS == ("RICH", "FAIR", "CHEAP", "UNKNOWN")


# ---------------------------------------------------------------------------
# Pass-through correctness -- never re-derived, always equal to the real
# upstream engines' own output.
# ---------------------------------------------------------------------------

class TestPassThrough:
    def test_market_regime_and_directional_bias_match_derive_trade_thesis(self):
        from bujji.msi_trade_thesis.engine import derive_trade_thesis
        kwargs = _full_kwargs()
        direct = derive_trade_thesis(kwargs["psi"], kwargs["mssi"], kwargs["mdi"], kwargs["mppi"], kwargs["vsb"], kwargs["consensus"], timestamp=TS)
        result = assess(**kwargs, timestamp=TS)
        assert result.market_regime == direct.thesis_type
        assert result.directional_bias == direct.directional_expectation
        assert result.volatility_environment == direct.volatility_expectation
        assert result.confidence == direct.conviction
        assert direct.assessment_id in result.supporting_assessment_ids

    def test_expected_move_environment_matches_vsb(self):
        result = assess(**_full_kwargs(vsb=_vsb(expected_move_state="WIDE")), timestamp=TS)
        assert result.expected_move_environment == "WIDE"

    def test_positioning_environment_exposes_mppi_directly(self):
        result = assess(**_full_kwargs(mppi=_mppi(positioning_bias="BULLISH_POSITIONING")), timestamp=TS)
        assert result.positioning_environment == "BULLISH_POSITIONING"


# ---------------------------------------------------------------------------
# premium_environment -- the one genuinely new translation.
# ---------------------------------------------------------------------------

class TestPremiumEnvironment:
    def test_prefers_volatility_intelligence_when_supplied(self):
        vi = assess_volatility_intelligence(volatility_structure=_vsb(iv_state="IV_CHEAP"), timestamp=TS)
        result = assess(**_full_kwargs(vsb=_vsb(iv_state="IV_RICH")), volatility_intelligence=vi, timestamp=TS)
        assert result.premium_environment == taxonomy.PREMIUM_CHEAP  # vi's own read wins over raw vsb

    def test_falls_back_to_vsb_when_no_volatility_intelligence(self):
        result = assess(**_full_kwargs(vsb=_vsb(iv_state="IV_CHEAP")), timestamp=TS)
        assert result.premium_environment == taxonomy.PREMIUM_CHEAP

    def test_unknown_when_neither_supplied(self):
        result = assess(**_full_kwargs(vsb=None), timestamp=TS)
        assert result.premium_environment == taxonomy.PREMIUM_UNKNOWN


# ---------------------------------------------------------------------------
# liquidity_environment
# ---------------------------------------------------------------------------

class TestLiquidityEnvironment:
    def test_exposes_real_tightness(self):
        result = assess(**_full_kwargs(), liquidity=_liquidity(tightness=SpreadTightness.WIDE), timestamp=TS)
        assert result.liquidity_environment == "WIDE"

    def test_unknown_when_absent(self):
        result = assess(**_full_kwargs(), timestamp=TS)
        assert result.liquidity_environment == "UNKNOWN"


# ---------------------------------------------------------------------------
# Strategy family grouping -- real SSF reshaping, never a new computation.
# ---------------------------------------------------------------------------

class TestFamilyGrouping:
    def test_every_family_appears_in_exactly_one_bucket(self):
        result = assess(**_full_kwargs(), liquidity=_liquidity(), timestamp=TS)
        all_named = set(result.preferred_strategy_families) | set(result.rejected_strategy_families) | set(result.insufficient_evidence_families)
        assert all_named == set(ssf_taxonomy.ALL_STRATEGY_FAMILIES)
        assert len(result.preferred_strategy_families) + len(result.rejected_strategy_families) + len(result.insufficient_evidence_families) == len(ssf_taxonomy.ALL_STRATEGY_FAMILIES)

    def test_insufficient_evidence_never_conflated_with_rejected(self):
        result = assess(**_full_kwargs(), liquidity=_liquidity(), timestamp=TS)
        assert set(result.rejected_strategy_families).isdisjoint(set(result.insufficient_evidence_families))

    def test_no_consensus_means_no_family_assessment_at_all(self):
        result = assess(**_full_kwargs(consensus=None), timestamp=TS)
        assert result.preferred_strategy_families == ()
        assert result.rejected_strategy_families == ()
        assert result.insufficient_evidence_families == ()
        assert any("ConsensusAssessment" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# Missing required evidence -- fails closed with a disclosed UNKNOWN
# assessment, never a crash, never a guess.
# ---------------------------------------------------------------------------

class TestMissingRequiredEvidence:
    @pytest.mark.parametrize("missing_key", ["psi", "mssi", "mdi", "mppi"])
    def test_missing_required_field_is_honest_unknown(self, missing_key):
        kwargs = _full_kwargs()
        kwargs[missing_key] = None
        result = assess(**kwargs, timestamp=TS)
        assert result.market_regime == "UNKNOWN"
        assert result.confidence == "NONE"
        assert result.preferred_strategy_families == ()
        assert any(missing_key in r for r in result.reasons)

    def test_all_four_missing(self):
        result = assess(timestamp=TS)
        assert result.market_regime == "UNKNOWN"
        assert result.confidence == "NONE"


# ---------------------------------------------------------------------------
# Conflicting evidence -- confirm EVENT_RISK (msi_trade_thesis's own real
# multi-domain-contradiction gate) passes through as market_regime.
# ---------------------------------------------------------------------------

class TestConflictingEvidence:
    def test_conflicted_structure_integrity_with_disagreeing_votes_yields_event_risk(self):
        result = assess(
            **_full_kwargs(
                psi=_psi(structure_integrity="CONFLICTED"),
                mssi=_mssi(breakout_state="CONFIRMED"),
                vsb=_vsb(compression_state="CONFIRMED", volatility_regime="COMPRESSED"),
            ),
            timestamp=TS,
        )
        assert result.market_regime == "EVENT_RISK"


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

class TestProvenance:
    def test_provenance_field(self):
        result = assess(**_full_kwargs(), timestamp=TS)
        assert result.provenance == "bujji.market_thesis.engine.assess"

    def test_supporting_ids_include_inputs_and_upstream_thesis(self):
        result = assess(**_full_kwargs(), timestamp=TS)
        assert "PSI-1" in result.supporting_assessment_ids
        assert "MSSI-1" in result.supporting_assessment_ids
        assert "MDI-1" in result.supporting_assessment_ids
        assert "MPPI-1" in result.supporting_assessment_ids
        assert "VSB-1" in result.supporting_assessment_ids
        assert "CON-1" in result.supporting_assessment_ids


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self):
        kwargs = _full_kwargs()
        a = assess(**kwargs, liquidity=_liquidity(), timestamp=TS)
        b = assess(**kwargs, liquidity=_liquidity(), timestamp=TS)
        assert a == b
