"""Tests -- Trade Thesis Bridge, Shadow Trading Brain Phase 5D.
No broker, no network -- directly constructed real assessment
dataclasses only."""
from __future__ import annotations

from bujji.msi_consensus.engine import compute_consensus
from bujji.msi_market_direction.engine import determine_market_direction
from bujji.msi_market_structure.models import Explanation as MssiExplanation
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_participant_positioning.models import Explanation as MppiExplanation
from bujji.msi_participant_positioning.models import MarketParticipantPositioningAssessment
from bujji.msi_price_structure.models import Explanation as PsiExplanation
from bujji.msi_price_structure.models import PriceStructureAssessment
from bujji.msi_volatility_structure.models import Explanation as VsbExplanation
from bujji.msi_volatility_structure.models import VolatilityStructureAssessment
from bujji.market_state.domain_view_adapter import build_domain_assessment_views
from bujji.market_state.trade_thesis_bridge import build_trade_thesis

TS = "2026-08-03T09:15:00+05:30"


def make_psi(trend_direction_signal="UP", confidence="MODERATE"):
    return PriceStructureAssessment(
        assessment_id="PSI-1", timestamp=TS, structure_state="RANGE_BOUND",
        trend_state="ESTABLISHED_TREND", swing_state="NONE", compression_state="NONE",
        expansion_state="NONE", balance_state="BALANCED", structure_integrity="COHERENT",
        confidence=confidence, supporting_episode_ids=("EP-1",), supporting_event_ids=("MEVT-1",),
        supporting_observation_ids=("OBS-1",), contradictions=(),
        explanation=PsiExplanation(
            assessment_id="PSI-1", what_changed=None, why=(), which_episodes_caused_it=("EP-1",),
            which_observations_support_it=("OBS-1",), missing_evidence=(), would_increase_confidence=(),
            schema_version="1.0",
        ),
        provenance="test", schema_version="1.0", trend_direction_signal=trend_direction_signal,
    )


def make_mssi(structure_location="ABOVE_RESISTANCE", breakout_state="CONFIRMED", breakdown_state="NONE", confidence="HIGH"):
    return MarketStructureAssessment(
        assessment_id="MSSI-1", timestamp=TS, structure_location=structure_location,
        support_state="NONE", resistance_state="NONE", breakout_state=breakout_state,
        breakdown_state=breakdown_state, retest_state="NONE", rejection_state="NONE",
        structural_balance="BALANCED", confidence=confidence,
        supporting_episode_ids=("EP-1",), supporting_event_ids=("MEVT-1",),
        supporting_observation_ids=("OBS-1",), contradictions=(),
        explanation=MssiExplanation(
            assessment_id="MSSI-1", what_changed=None, why=(), which_episodes_caused_it=("EP-1",),
            which_observations_support_it=("OBS-1",), missing_evidence=(), would_increase_confidence=(),
            schema_version="1.0",
        ),
        provenance="test", schema_version="1.0",
    )


def make_mppi(positioning_bias="BULLISH_POSITIONING", positioning_strength="MODERATE"):
    return MarketParticipantPositioningAssessment(
        assessment_id="MPPI-1", timestamp=TS, positioning_bias=positioning_bias,
        positioning_strength=positioning_strength, participating_lenses=(), conflicting_lenses=(),
        supporting_observation_ids=("OBS-2",),
        explanation=MppiExplanation(
            assessment_id="MPPI-1", which_lenses_participated=(), which_bullish=(), which_bearish=(),
            which_neutral_or_unknown=(), per_lens_evidence=(), missing_evidence=(),
            why_positioning_was_chosen="test", schema_version="1.0",
        ),
        provenance="test", schema_version="1.0",
    )


def make_vsb(volatility_regime="NORMAL", confidence="MODERATE", expected_move_pct=1.2):
    return VolatilityStructureAssessment(
        assessment_id="VSB-1", timestamp=TS, volatility_regime=volatility_regime,
        iv_state="NORMAL", expected_move_state="NORMAL", skew_state="UNKNOWN",
        term_structure_state="UNKNOWN", expansion_state="NONE", compression_state="NONE",
        confidence=confidence, iv_average=0.15, realized_vol=0.14, expected_move_pct=expected_move_pct,
        explanation=VsbExplanation(assessment_id="VSB-1", why=(), missing_evidence=(), would_increase_confidence=(), schema_version="1.0"),
        provenance="test", schema_version="1.0",
    )


def make_mdi(psi, mssi):
    return determine_market_direction(psi, mssi, timestamp=TS)


def make_consensus(psi, mssi, mdi, mppi):
    views = build_domain_assessment_views(psi, mssi, mdi, mppi)
    return compute_consensus(views, timestamp=TS)


# --- Functional tests ---

def test_complete_msi_chain_creates_trade_thesis_assessment():
    psi, mssi, mppi, vsb = make_psi(), make_mssi(), make_mppi(), make_vsb()
    mdi = make_mdi(psi, mssi)
    consensus = make_consensus(psi, mssi, mdi, mppi)

    thesis = build_trade_thesis(psi, mssi, mdi, mppi, vsb, consensus, TS)

    assert thesis is not None
    assert thesis.assessment_id
    assert thesis.thesis_type


def test_bullish_agreement_produces_bullish_thesis():
    psi, mssi, mppi, vsb = make_psi("UP"), make_mssi(breakout_state="CONFIRMED"), make_mppi("BULLISH_POSITIONING"), make_vsb()
    mdi = make_mdi(psi, mssi)
    consensus = make_consensus(psi, mssi, mdi, mppi)

    thesis = build_trade_thesis(psi, mssi, mdi, mppi, vsb, consensus, TS)

    assert thesis.directional_expectation == "STRONG_BULLISH"
    assert thesis.thesis_type == "BREAKOUT"
    assert thesis.conflicting_domains == ()


def test_conflicting_evidence_preserves_conflicts_never_resolved():
    # Price says UP, market structure says breakdown -> mdi itself
    # becomes MIXED (per Phase 3D's own established behavior).
    psi = make_psi("UP")
    mssi = make_mssi(structure_location="BELOW_SUPPORT", breakdown_state="CONFIRMED", breakout_state="NONE")
    mdi = make_mdi(psi, mssi)
    mppi = make_mppi("BULLISH_POSITIONING")
    vsb = make_vsb()
    consensus = make_consensus(psi, mssi, mdi, mppi)

    thesis = build_trade_thesis(psi, mssi, mdi, mppi, vsb, consensus, TS)

    assert thesis is not None
    assert thesis.directional_expectation == "MIXED"
    # A real conflict must remain visible somewhere in the assessment --
    # never silently forced into agreement.
    assert thesis.conflicting_domains != () or "MIXED" in thesis.directional_expectation


def test_missing_consensus_remains_safe_no_crash():
    psi, mssi, mppi, vsb = make_psi(), make_mssi(), make_mppi(), make_vsb()
    mdi = make_mdi(psi, mssi)

    thesis = build_trade_thesis(psi, mssi, mdi, mppi, vsb, consensus=None, timestamp=TS)

    assert thesis is not None  # engine's own None-safety for consensus, confirmed by Phase 5C
    assert thesis.thesis_type


def test_missing_vsb_remains_honest_no_fabricated_expected_move():
    psi, mssi, mppi = make_psi(), make_mssi(), make_mppi()
    mdi = make_mdi(psi, mssi)
    consensus = make_consensus(psi, mssi, mdi, mppi)

    thesis = build_trade_thesis(psi, mssi, mdi, mppi, vsb=None, consensus=consensus, timestamp=TS)

    assert thesis is not None
    assert thesis.expected_move is None  # honestly absent, never guessed


def test_no_trade_thesis_is_a_classification_not_an_instruction():
    # No directional/structural votes at all -> genuinely THESIS_NO_TRADE.
    psi = make_psi(trend_direction_signal=None)
    mssi = make_mssi(structure_location="INSIDE_RANGE", breakout_state="NONE", breakdown_state="NONE")
    mdi = make_mdi(psi, mssi)
    mppi = make_mppi("NEUTRAL_POSITIONING")
    consensus = make_consensus(psi, mssi, mdi, mppi)

    thesis = build_trade_thesis(psi, mssi, mdi, mppi, vsb=None, consensus=consensus, timestamp=TS)

    assert thesis.thesis_type == "NO_TRADE"
    assert thesis.conviction == "NONE"
    # Structural guard: even the NO_TRADE thesis carries no instruction
    # field -- confirmed by the model's own field set (see safety test),
    # not by string-matching this object.
    assert not hasattr(thesis, "action")
    assert not hasattr(thesis, "order")


def test_missing_psi_mssi_mdi_or_mppi_returns_none_never_fabricated():
    psi, mssi, mppi, vsb = make_psi(), make_mssi(), make_mppi(), make_vsb()
    mdi = make_mdi(psi, mssi)
    consensus = make_consensus(psi, mssi, mdi, mppi)

    assert build_trade_thesis(None, mssi, mdi, mppi, vsb, consensus, TS) is None
    assert build_trade_thesis(psi, None, mdi, mppi, vsb, consensus, TS) is None
    assert build_trade_thesis(psi, mssi, None, mppi, vsb, consensus, TS) is None
    assert build_trade_thesis(psi, mssi, mdi, None, vsb, consensus, TS) is None


# --- Integration tests ---

def test_real_msi_objects_flow_into_derive_trade_thesis_unmodified():
    psi, mssi, mppi, vsb = make_psi(), make_mssi(), make_mppi(), make_vsb()
    mdi = make_mdi(psi, mssi)
    consensus = make_consensus(psi, mssi, mdi, mppi)

    thesis = build_trade_thesis(psi, mssi, mdi, mppi, vsb, consensus, TS)

    # Provenance confirms the real, unmodified engine produced this --
    # not a re-implementation inside the bridge.
    assert thesis.provenance == "bujji.msi_trade_thesis.engine.derive_trade_thesis"


def test_output_is_a_real_trade_thesis_assessment_type():
    from bujji.msi_trade_thesis.models import TradeThesisAssessment
    psi, mssi, mppi, vsb = make_psi(), make_mssi(), make_mppi(), make_vsb()
    mdi = make_mdi(psi, mssi)
    consensus = make_consensus(psi, mssi, mdi, mppi)

    thesis = build_trade_thesis(psi, mssi, mdi, mppi, vsb, consensus, TS)
    assert isinstance(thesis, TradeThesisAssessment)
