"""Tests -- Market Direction Bridge, Shadow Campaign v2 Phase 3D.
No broker, no network -- directly constructed real assessment
dataclasses only."""
from __future__ import annotations

from bujji.market_state.direction_bridge import build_market_direction
from bujji.market_state.synthesizer import build_market_state
from bujji.market_state_builder.assessment_bridge import MarketStateAssessment
from bujji.msi_market_structure.models import Explanation as MssiExplanation
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_price_structure.models import Explanation as PsiExplanation
from bujji.msi_price_structure.models import PriceStructureAssessment

TS = "2026-08-03T09:15:00+05:30"


def make_price_structure(trend_direction_signal=None, trend_state="ESTABLISHED_TREND"):
    return PriceStructureAssessment(
        assessment_id="PSI-1", timestamp=TS, structure_state="RANGE_BOUND",
        trend_state=trend_state, swing_state="NONE", compression_state="NONE",
        expansion_state="NONE", balance_state="BALANCED", structure_integrity="COHERENT",
        confidence="MODERATE", supporting_episode_ids=("EP-1",), supporting_event_ids=("MEVT-1",),
        supporting_observation_ids=("OBS-1",), contradictions=(),
        explanation=PsiExplanation(
            assessment_id="PSI-1", what_changed=None, why=(), which_episodes_caused_it=("EP-1",),
            which_observations_support_it=("OBS-1",), missing_evidence=(), would_increase_confidence=(),
            schema_version="1.0",
        ),
        provenance="test", schema_version="1.0", trend_direction_signal=trend_direction_signal,
    )


def make_market_structure(structure_location="INSIDE_RANGE", breakout_state="NONE", breakdown_state="NONE"):
    return MarketStructureAssessment(
        assessment_id="MSSI-1", timestamp=TS, structure_location=structure_location,
        support_state="NONE", resistance_state="NONE", breakout_state=breakout_state,
        breakdown_state=breakdown_state, retest_state="NONE", rejection_state="NONE",
        structural_balance="BALANCED", confidence="MODERATE",
        supporting_episode_ids=("EP-1",), supporting_event_ids=("MEVT-1",),
        supporting_observation_ids=("OBS-1",), contradictions=(),
        explanation=MssiExplanation(
            assessment_id="MSSI-1", what_changed=None, why=(), which_episodes_caused_it=("EP-1",),
            which_observations_support_it=("OBS-1",), missing_evidence=(), would_increase_confidence=(),
            schema_version="1.0",
        ),
        provenance="test", schema_version="1.0",
    )


def make_assessment(price_structure=None, market_structure=None):
    return MarketStateAssessment(
        price_structure=price_structure, market_structure=market_structure,
        participant_positioning=None, events=(), episodes=(),
    )


# --- Agreement ---

def test_price_up_and_confirmed_breakout_agree_produces_bullish_direction():
    assessment = make_assessment(
        price_structure=make_price_structure("UP"),
        market_structure=make_market_structure("ABOVE_RESISTANCE", breakout_state="CONFIRMED"),
    )
    mdi = build_market_direction(assessment, TS)
    assert mdi is not None
    assert mdi.overall_direction == "STRONG_BULLISH"
    assert mdi.overall_confidence == "HIGH"
    assert mdi.conflicting_lenses == ()


# --- Conflict ---

def test_price_up_and_confirmed_breakdown_conflict_lowers_confidence():
    assessment = make_assessment(
        price_structure=make_price_structure("UP"),
        market_structure=make_market_structure("BELOW_SUPPORT", breakdown_state="CONFIRMED"),
    )
    mdi = build_market_direction(assessment, TS)
    assert mdi is not None
    # Genuine disagreement -- reconcile_lenses' own real behavior:
    # MIXED direction, never averaged/forced into one side.
    assert mdi.overall_direction == "MIXED"
    assert mdi.overall_confidence == "LOW"
    assert mdi.conflicting_lenses != ()


def test_conflict_surfaces_as_uncertainty_on_market_state():
    assessment = make_assessment(
        price_structure=make_price_structure("UP"),
        market_structure=make_market_structure("BELOW_SUPPORT", breakdown_state="CONFIRMED"),
    )
    state = build_market_state({}, assessment, TS)
    assert state.market_direction.direction == "MIXED"
    assert state.market_direction.confidence == "LOW"
    assert any("conflict" in u for u in state.market_direction.uncertainties)


# --- Missing structure ---

def test_missing_price_structure_returns_none():
    assessment = make_assessment(price_structure=None, market_structure=make_market_structure("ABOVE_RESISTANCE"))
    assert build_market_direction(assessment, TS) is None


def test_missing_market_structure_returns_none():
    assessment = make_assessment(price_structure=make_price_structure("UP"), market_structure=None)
    assert build_market_direction(assessment, TS) is None


def test_missing_structure_on_market_state_is_honestly_none():
    assessment = make_assessment()  # both None
    state = build_market_state({}, assessment, TS)
    assert state.market_direction.direction is None
    assert state.market_direction.confidence is None
    assert state.market_direction.evidence == ()


def test_market_direction_never_forces_a_direction_when_only_spatial_evidence():
    # structure_location purely spatial (INSIDE_RANGE) -> market
    # structure lens is honestly UNKNOWN, never guessed from
    # breakout/breakdown flags alone outside AT_RETEST/ABOVE/BELOW.
    assessment = make_assessment(
        price_structure=make_price_structure(None, trend_state="NO_TREND"),
        market_structure=make_market_structure("INSIDE_RANGE"),
    )
    mdi = build_market_direction(assessment, TS)
    assert mdi is not None
    assert mdi.overall_direction == "UNKNOWN"
    assert mdi.overall_confidence == "NONE"
