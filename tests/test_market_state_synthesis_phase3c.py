"""Tests -- Market State Synthesis, Shadow Campaign v2 Phase 3C.
No broker, no network -- fake intelligence_snapshot dicts and directly
constructed real assessment dataclasses only."""
from __future__ import annotations

from bujji.live_market_events.models import MarketEvent, MarketEventProvenance
from bujji.market_episode.models import Episode, EpisodeProvenance
from bujji.market_state.confidence import (
    CONFIDENCE_HIGH, CONFIDENCE_LOW, CONFIDENCE_MODERATE, CONFIDENCE_UNKNOWN,
)
from bujji.market_state.synthesizer import build_market_state
from bujji.market_state_builder.assessment_bridge import MarketStateAssessment
from bujji.msi_market_structure.models import Explanation as MssiExplanation
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_participant_positioning.models import Explanation as MppiExplanation
from bujji.msi_participant_positioning.models import MarketParticipantPositioningAssessment
from bujji.msi_price_structure.models import Explanation as PsiExplanation
from bujji.msi_price_structure.models import PriceStructureAssessment

TS = "2026-08-03T09:15:00+05:30"


def make_price_structure(trend_direction_signal=None, structure_state="RANGE_BOUND"):
    return PriceStructureAssessment(
        assessment_id="PSI-1", timestamp=TS, structure_state=structure_state,
        trend_state="ESTABLISHED_TREND", swing_state="NONE", compression_state="NONE",
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


def make_market_structure(breakout_state="NONE", breakdown_state="NONE", structure_location="MID_RANGE"):
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


def make_participant_positioning(positioning_bias="NEUTRAL_POSITIONING"):
    return MarketParticipantPositioningAssessment(
        assessment_id="MPPI-1", timestamp=TS, positioning_bias=positioning_bias,
        positioning_strength="MODERATE", participating_lenses=(), conflicting_lenses=(),
        supporting_observation_ids=("OBS-2",),
        explanation=MppiExplanation(
            assessment_id="MPPI-1", which_lenses_participated=(), which_bullish=(), which_bearish=(),
            which_neutral_or_unknown=(), per_lens_evidence=(), missing_evidence=(),
            why_positioning_was_chosen="test", schema_version="1.0",
        ),
        provenance="test", schema_version="1.0",
    )


def make_event(event_type="PRICE_CHANGED"):
    return MarketEvent(
        event_id=f"MEVT-{event_type}", event_type=event_type, timestamp=TS,
        originating_observation_ids=("OBS-1",), detail={},
        provenance=MarketEventProvenance(originating_source="test", detection_context="LIVE", schema_version="1.0"),
        schema_version="1.0",
    )


def make_episode(episode_id="EP-1"):
    return Episode(
        episode_id=episode_id, episode_type="PRICE_MOVEMENT_EPISODE", start_time=TS, latest_update=TS,
        end_time=None, originating_event_ids=("MEVT-1",), originating_observation_ids=("OBS-1",),
        current_state="ACTIVE",
        provenance=EpisodeProvenance(originating_source="test", detection_context="LIVE", schema_version="1.0"),
        schema_version="1.0",
    )


def make_assessment(price_structure=None, market_structure=None, participant_positioning=None,
                     events=(), episodes=()):
    return MarketStateAssessment(
        price_structure=price_structure, market_structure=market_structure,
        participant_positioning=participant_positioning, events=events, episodes=episodes,
    )


# --- Complete data ---

def test_complete_data_produces_valid_market_state():
    intel = {
        "regime": {"regime": "TRENDING", "confidence": 0.8, "data_quality": "SUFFICIENT"},
        "liquidity": {"tightness": "TIGHT", "data_quality": "SUFFICIENT"},
        "volatility": {"richness": "NORMAL", "data_quality": "SUFFICIENT"},
    }
    assessment = make_assessment(
        price_structure=make_price_structure("UP"),
        market_structure=make_market_structure(breakout_state="CONFIRMED"),
        participant_positioning=make_participant_positioning("BULLISH_POSITIONING"),
        events=(make_event(),), episodes=(make_episode(),),
    )
    state = build_market_state(intel, assessment, TS)
    assert state.regime == "TRENDING"
    assert state.volatility_state == "NORMAL"
    assert state.liquidity_state == "TIGHT"
    assert state.price_structure == "RANGE_BOUND"
    assert state.market_structure == "MID_RANGE"
    assert state.participant_positioning == "BULLISH_POSITIONING"
    assert state.active_events == ("PRICE_CHANGED",)
    assert state.active_episodes == ("EP-1",)
    assert state.evidence_ids  # non-empty, real ids threaded through
    assert state.overall_confidence == CONFIDENCE_HIGH  # 3 real sources, all agree UP


# --- Missing data ---

def test_missing_vix_and_volatility_produces_unknown_field_not_crash():
    intel = {"regime": {"regime": "RANGING"}, "liquidity": {"tightness": "WIDE"}}
    assessment = make_assessment()  # no episodes/events/assessments at all
    state = build_market_state(intel, assessment, TS)
    assert state.volatility_state is None  # honestly absent, never fabricated
    assert state.price_structure is None
    assert state.market_structure is None
    assert state.participant_positioning is None
    assert state.overall_confidence == CONFIDENCE_UNKNOWN
    assert any("volatility unavailable" in u for u in state.uncertainties)
    assert any("no active episodes" in u for u in state.uncertainties)


def test_missing_oi_i_e_no_participant_positioning_still_produces_state():
    intel = {"regime": {"regime": "RANGING"}}
    assessment = make_assessment(
        price_structure=make_price_structure("UP"), market_structure=make_market_structure(breakout_state="CONFIRMED"),
        participant_positioning=None, events=(make_event(),), episodes=(make_episode(),),
    )
    state = build_market_state(intel, assessment, TS)
    assert state.participant_positioning is None
    assert any("participant positioning unavailable" in u for u in state.uncertainties)


def test_missing_episodes_produces_unknown_price_and_market_structure():
    intel = {}
    assessment = make_assessment()  # episodes=(), events=()
    state = build_market_state(intel, assessment, TS)
    assert state.price_structure is None
    assert state.market_structure is None
    assert state.active_episodes == ()
    assert any("no active episodes" in u for u in state.uncertainties)


# --- Conflicting data ---

def test_conflicting_price_and_market_structure_lowers_confidence_without_resolving():
    intel = {"regime": {"regime": "RANGING"}}
    assessment = make_assessment(
        price_structure=make_price_structure(trend_direction_signal="UP"),
        market_structure=make_market_structure(breakdown_state="CONFIRMED"),  # DOWN signal -- genuine conflict
        events=(make_event(),), episodes=(make_episode(),),
    )
    state = build_market_state(intel, assessment, TS)
    # Both real facts are reported as-is, never overridden/forced into agreement.
    assert state.price_structure == "RANGE_BOUND"
    assert state.market_structure == "MID_RANGE"
    assert state.overall_confidence in (CONFIDENCE_LOW,)
    assert any("conflicting directional signals" in u for u in state.uncertainties)


def test_agreeing_price_and_positioning_do_not_force_a_conflict():
    intel = {"regime": {"regime": "TRENDING"}}
    assessment = make_assessment(
        price_structure=make_price_structure(trend_direction_signal="UP"),
        participant_positioning=make_participant_positioning("BULLISH_POSITIONING"),
        events=(make_event(),), episodes=(make_episode(),),
    )
    state = build_market_state(intel, assessment, TS)
    assert not any("conflicting" in u for u in state.uncertainties)
    assert state.overall_confidence in (CONFIDENCE_MODERATE, CONFIDENCE_HIGH)


def test_no_scoring_fields_exist_on_market_state():
    # Structural guard: no bullish_score/buy_probability/etc. field could
    # even be populated, because the dataclass has no such field at all.
    from bujji.market_state.models import MarketState
    field_names = set(MarketState.__dataclass_fields__.keys())
    forbidden = {"bullish_score", "bearish_score", "buy_probability", "sell_probability", "signal_strength"}
    assert not (field_names & forbidden)
