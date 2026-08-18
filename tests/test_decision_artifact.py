"""Tests for Decision Artifact -- composes real intelligence_orchestrator
+ market_thesis + strategy_evaluator output into one journaled record.
Builds real end-to-end DecisionTrace/MarketThesisAssessment/RankedCandidates
via the actual orchestrate() pipeline (Phase 4) rather than mocking them."""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from bujji.decision_artifact import DecisionArtifactJournal, build_decision_artifact
from bujji.intelligence.models import LiquidityReading, SpreadTightness
from bujji.intelligence_orchestrator import DecisionContext, evaluate_strategies, generate_thesis, orchestrate
from bujji.msi_consensus.models import Explanation as ConsensusExplanation, ConsensusAssessment
from bujji.msi_consensus import taxonomy as consensus_taxonomy
from bujji.msi_market_direction.models import Explanation as MdiExplanation, MarketDirectionAssessment
from bujji.msi_market_structure.models import Explanation as MssiExplanation, MarketStructureAssessment
from bujji.msi_participant_positioning.models import (
    Explanation as MppiExplanation, MarketParticipantPositioningAssessment,
)
from bujji.msi_price_structure.models import Explanation as PsiExplanation, PriceStructureAssessment
from bujji.msi_volatility_structure.models import Explanation as VsbExplanation, VolatilityStructureAssessment
from bujji.outcome_attribution.models import OUTCOME_LOSS, OUTCOME_PROFIT
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


def _liquidity(tightness=SpreadTightness.TIGHT):
    return LiquidityReading(
        ce_bid=100.0, ce_ask=101.0, pe_bid=100.0, pe_ask=101.0, ce_spread_pct=1.0, pe_spread_pct=1.0,
        combined_spread=2.0, combined_spread_pct=1.0, tightness=tightness, confidence=0.9, reason="test",
    )


def _market_state_assessment(**overrides):
    base = dict(
        assessment_id="MSA-1", market_state="RANGE", market_phase="ESTABLISHED", market_character="CLEAR",
        market_conviction="VERY_HIGH", confidence="VERY_HIGH", supporting_evidence=("stub",),
        contradicting_evidence=(), reasoning_trace="stub", interpretation_id="EI-1", timestamp=TS, version="1.0.0",
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


class _StubOutcome:
    def __init__(self, outcome_direction, realized_pnl, entry_timestamp="2026-01-01T09:20:00", exit_timestamp="2026-01-01T15:20:00"):
        self.outcome_direction = outcome_direction
        self.realized_pnl = realized_pnl
        self.entry_timestamp = entry_timestamp
        self.exit_timestamp = exit_timestamp


class _StubLeg:
    def __init__(self, side, option_type, strike):
        self.side, self.option_type, self.strike = side, option_type, strike


class _StubConstruction:
    def __init__(self, constructed, legs=()):
        self.constructed = constructed
        self.legs = legs


# ---------------------------------------------------------------------------
# Composition from the real end-to-end pipeline.
# ---------------------------------------------------------------------------

class TestBuildFromRealPipeline:
    def test_trade_case(self):
        context = _full_context()
        thesis = generate_thesis(context)
        decision, ranked = evaluate_strategies(context, clock=FIXED_CLOCK)
        trace = orchestrate(context, clock=FIXED_CLOCK)

        artifact = build_decision_artifact(trace, thesis=thesis, ranked=ranked, session_id="SESS-1")
        assert artifact.session_id == "SESS-1"
        assert artifact.market_thesis_assessment_id == trace.market_thesis_assessment_id
        assert artifact.market_regime == thesis.market_regime
        assert artifact.directional_bias == thesis.directional_bias
        assert artifact.selected_strategy == trace.outcome.selected_strategy
        assert artifact.confidence == trace.outcome.confidence
        assert artifact.steps == trace.steps
        assert artifact.trade_constructed is False
        assert artifact.learning_tag in ("OPEN", "NO_TRADE")

    def test_no_trade_case(self):
        context = _full_context(market_state_assessment=None)
        trace = orchestrate(context, clock=FIXED_CLOCK)
        artifact = build_decision_artifact(trace)
        assert artifact.selected_strategy is None
        assert artifact.learning_tag == "NO_TRADE"
        assert artifact.candidate_strategies == ()

    def test_missing_thesis_and_ranked_are_honest_none(self):
        context = _full_context()
        trace = orchestrate(context, clock=FIXED_CLOCK)
        artifact = build_decision_artifact(trace)
        assert artifact.market_regime is None
        assert artifact.directional_bias is None
        assert artifact.candidate_strategies == ()


# ---------------------------------------------------------------------------
# Trade construction / outcome composition, and learning_tag derivation.
# ---------------------------------------------------------------------------

class TestConstructionAndOutcome:
    def test_constructed_trade_produces_leg_summary(self):
        context = _full_context()
        trace = orchestrate(context, clock=FIXED_CLOCK)
        construction = _StubConstruction(constructed=True, legs=(_StubLeg("SELL", "CE", 22600), _StubLeg("SELL", "PE", 22400)))
        artifact = build_decision_artifact(trace, construction=construction)
        assert artifact.trade_constructed is True
        assert artifact.trade_legs_summary == ("SELL CE22600", "SELL PE22400")
        assert artifact.learning_tag == "OPEN"

    def test_unconstructed_trade_has_no_legs(self):
        context = _full_context()
        trace = orchestrate(context, clock=FIXED_CLOCK)
        construction = _StubConstruction(constructed=False)
        artifact = build_decision_artifact(trace, construction=construction)
        assert artifact.trade_constructed is False
        assert artifact.trade_legs_summary == ()

    @pytest.mark.parametrize("outcome_direction,expected_tag", [
        (OUTCOME_PROFIT, "WIN"), (OUTCOME_LOSS, "LOSS"),
    ])
    def test_outcome_maps_to_learning_tag(self, outcome_direction, expected_tag):
        context = _full_context()
        trace = orchestrate(context, clock=FIXED_CLOCK)
        construction = _StubConstruction(constructed=True, legs=(_StubLeg("SELL", "CE", 22600),))
        outcome = _StubOutcome(outcome_direction, realized_pnl=1500.0)
        artifact = build_decision_artifact(trace, construction=construction, outcome=outcome)
        assert artifact.learning_tag == expected_tag
        assert artifact.realized_pnl == 1500.0
        assert artifact.entry_timestamp == "2026-01-01T09:20:00"
        assert artifact.exit_timestamp == "2026-01-01T15:20:00"


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

class TestJournal:
    def test_append_and_read_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = DecisionArtifactJournal(str(Path(tmp) / "decisions.jsonl"))
            context = _full_context()
            trace = orchestrate(context, clock=FIXED_CLOCK)
            artifact = build_decision_artifact(trace)
            journal.append(artifact)
            records = journal.read_all()
            assert len(records) == 1
            assert records[0] == artifact

    def test_find_by_decision_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = DecisionArtifactJournal(str(Path(tmp) / "decisions.jsonl"))
            context = _full_context()
            trace = orchestrate(context, clock=FIXED_CLOCK)
            artifact = build_decision_artifact(trace)
            journal.append(artifact)
            found = journal.find_by_decision_id(artifact.decision_id)
            assert found == artifact
            assert journal.find_by_decision_id("NOT-REAL") is None

    def test_find_by_learning_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = DecisionArtifactJournal(str(Path(tmp) / "decisions.jsonl"))
            context = _full_context(market_state_assessment=None)
            trace = orchestrate(context, clock=FIXED_CLOCK)
            journal.append(build_decision_artifact(trace))
            assert len(journal.find_by_learning_tag("NO_TRADE")) == 1
            assert len(journal.find_by_learning_tag("WIN")) == 0

    def test_journal_is_append_only_across_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "decisions.jsonl")
            context = _full_context()
            trace = orchestrate(context, clock=FIXED_CLOCK)
            artifact = build_decision_artifact(trace)
            DecisionArtifactJournal(path).append(artifact)
            DecisionArtifactJournal(path).append(artifact)
            assert len(DecisionArtifactJournal(path).read_all()) == 2
