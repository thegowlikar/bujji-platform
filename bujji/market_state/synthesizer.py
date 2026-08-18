"""Market State Synthesizer -- Shadow Campaign v2 Phase 3C.

Combines intelligence.runner.run_intelligence()'s dict output with
market_state_builder's MarketStateAssessment into one immutable
MarketState -- pure description, never a decision. No scoring logic
(bullish_score/buy_probability/sell_probability/etc.) exists anywhere
in this module, per this phase's own explicit rule; every field is a
direct pass-through of an already-computed real state label.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from .confidence import compute_confidence
from .direction_bridge import build_market_direction
from .models import MarketDirectionSummary, MarketState


def _evidence_ids(market_state_assessment) -> Tuple[str, ...]:
    ids = set()
    for assessment in (market_state_assessment.price_structure, market_state_assessment.market_structure):
        if assessment is not None:
            ids.update(assessment.supporting_episode_ids)
            ids.update(assessment.supporting_event_ids)
            ids.update(assessment.supporting_observation_ids)
    if market_state_assessment.participant_positioning is not None:
        ids.update(market_state_assessment.participant_positioning.supporting_observation_ids)
    return tuple(sorted(ids))


def build_market_state(
    intelligence_snapshot: Dict[str, Any], market_state_assessment, timestamp: str,
) -> MarketState:
    """intelligence_snapshot is run_intelligence()'s own return dict
    (Phase 2's build_intelligence_snapshot output); market_state_assessment
    is market_state_builder's MarketStateAssessment (Phase 3B). Both are
    consumed exactly as-shaped, never re-derived or reinterpreted."""
    regime = intelligence_snapshot.get("regime", {}).get("regime")
    volatility_state = intelligence_snapshot.get("volatility", {}).get("richness")
    liquidity_state = intelligence_snapshot.get("liquidity", {}).get("tightness")

    price_structure = (
        market_state_assessment.price_structure.structure_state
        if market_state_assessment.price_structure is not None else None
    )
    market_structure = (
        market_state_assessment.market_structure.structure_location
        if market_state_assessment.market_structure is not None else None
    )
    participant_positioning = (
        market_state_assessment.participant_positioning.positioning_bias
        if market_state_assessment.participant_positioning is not None else None
    )

    overall_confidence, uncertainties = compute_confidence(intelligence_snapshot, market_state_assessment)
    market_direction = _build_market_direction_summary(market_state_assessment, timestamp)

    return MarketState(
        timestamp=timestamp,
        regime=regime, volatility_state=volatility_state, liquidity_state=liquidity_state,
        price_structure=price_structure, market_structure=market_structure,
        participant_positioning=participant_positioning,
        active_events=tuple(e.event_type for e in market_state_assessment.events),
        active_episodes=tuple(ep.episode_id for ep in market_state_assessment.episodes),
        overall_confidence=overall_confidence, uncertainties=uncertainties,
        evidence_ids=_evidence_ids(market_state_assessment),
        market_direction=market_direction,
    )


def _build_market_direction_summary(market_state_assessment, timestamp: str) -> MarketDirectionSummary:
    """Phase 3D. Reuses msi_market_direction's own real confidence
    output (never recomputed here) -- direction/confidence are None
    only when PriceStructureAssessment/MarketStructureAssessment are
    themselves honestly absent (direction_bridge returns None). A
    genuine lens conflict still produces a real MarketDirectionAssessment
    (direction=MIXED, confidence=LOW per msi_market_direction's own
    reconcile_lenses) -- conflicting_lenses is surfaced here as an
    uncertainty, never silently dropped."""
    mdi = build_market_direction(market_state_assessment, timestamp)
    if mdi is None:
        return MarketDirectionSummary(direction=None, confidence=None, evidence=(), uncertainties=())
    uncertainties = ()
    if mdi.conflicting_lenses:
        uncertainties = (
            f"market direction conflict among lenses: {mdi.conflicting_lenses}",
        )
    return MarketDirectionSummary(
        direction=mdi.overall_direction, confidence=mdi.overall_confidence,
        evidence=mdi.supporting_assessment_ids, uncertainties=uncertainties,
    )
