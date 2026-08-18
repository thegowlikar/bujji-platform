"""build_market_state_node() -- Phase 19.8.

The one entry point. Pure composition over already-built objects from
every prior layer -- never calls a brain's `.analyze(`, never
instantiates a broker, never queries a datastore itself. Every input
(current/previous snapshot, decision intelligence, current/previous
phenomena assessment, previous node) is handed in by the caller. No
wall-clock call anywhere in this module.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Optional

from bujji.decision_intelligence.models import DecisionIntelligenceSnapshot
from bujji.intelligence.market_intelligence_snapshot.models import MarketIntelligenceSnapshot
from bujji.market_phenomena.models import PHENOMENON_EVENT_RISK, MarketPhenomenaAssessment

from .models import CONFIDENCE_HIGH, CONFIDENCE_LOW, CONFIDENCE_MODERATE, CONFIDENCE_NONE, MarketStateNode, StateTransitionEdge
from .transitions import (
    LIQUIDITY_NORMAL_TO_STRESS,
    LIQUIDITY_STRESS_RECOVERY,
    NORMAL_TO_EVENT_RISK,
    UNKNOWN,
    detect_liquidity_transition_type,
    detect_posture_transition_type,
)


def _observation_confidence(decision_intelligence: DecisionIntelligenceSnapshot) -> str:
    """Reuses `DecisionIntelligenceSnapshot.evidence_bundle["confidence"]`
    verbatim (Phase 19.6's own already-real, min-across-brains
    confidence) -- never a new number. Bucketed with the same
    LOW/MODERATE/HIGH bands `market_understanding.memory_similarity`
    already established for confidence banding."""
    confidence = decision_intelligence.evidence_bundle.get("confidence", 0.0)
    if confidence >= 0.8:
        return CONFIDENCE_HIGH
    if confidence >= 0.5:
        return CONFIDENCE_MODERATE
    return CONFIDENCE_LOW


def _choose_transition(
    previous_snapshot: Optional[MarketIntelligenceSnapshot], current_snapshot: MarketIntelligenceSnapshot,
    previous_phenomena: Optional[MarketPhenomenaAssessment], current_phenomena: MarketPhenomenaAssessment,
) -> Optional[tuple]:
    """One documented priority order when more than one real transition
    is detected in the same cycle: event risk first (the highest-stakes
    real change), then liquidity stress onset/recovery, then posture-
    shaped transitions. Returns (transition_type, evidence) or None."""
    liquidity_result = detect_liquidity_transition_type(previous_phenomena, current_phenomena)

    posture_type, posture_evidence = (
        detect_posture_transition_type(previous_snapshot, current_snapshot) if previous_snapshot is not None
        else (UNKNOWN, ())
    )

    if posture_type == NORMAL_TO_EVENT_RISK:
        return posture_type, posture_evidence
    if liquidity_result is not None:
        return liquidity_result
    if posture_type != UNKNOWN:
        return posture_type, posture_evidence
    return None


def build_market_state_node(
    *,
    snapshot: MarketIntelligenceSnapshot,
    decision_intelligence: DecisionIntelligenceSnapshot,
    phenomena_assessment: MarketPhenomenaAssessment,
    previous_snapshot: Optional[MarketIntelligenceSnapshot] = None,
    previous_phenomena_assessment: Optional[MarketPhenomenaAssessment] = None,
    previous_node: Optional[MarketStateNode] = None,
) -> MarketStateNode:
    regime = snapshot.regime.regime.value if snapshot.regime.regime else None
    volatility_state = snapshot.volatility.richness.value if snapshot.volatility.richness else None
    liquidity_state = snapshot.liquidity.tightness.value if snapshot.liquidity.tightness else None
    event_state = "EVENT_RISK" if any(
        p.phenomenon_type == PHENOMENON_EVENT_RISK for p in phenomena_assessment.phenomena
    ) else "NORMAL"
    decision_posture = decision_intelligence.recommended_posture.value

    evidence_bundle = {
        "decision_evidence": decision_intelligence.evidence_bundle,
        "phenomena": [p.to_dict() for p in phenomena_assessment.phenomena],
    }

    transition_choice = _choose_transition(
        previous_snapshot, snapshot, previous_phenomena_assessment, phenomena_assessment,
    )
    transition = None
    if transition_choice is not None:
        transition_type, transition_evidence = transition_choice
        observation_confidence = _observation_confidence(decision_intelligence)
        interpretation_confidence = (
            CONFIDENCE_HIGH if len(transition_evidence) >= 2
            else (CONFIDENCE_MODERATE if transition_evidence else CONFIDENCE_LOW)
        )
        transition = StateTransitionEdge(
            transition_type=transition_type,
            from_state_id=previous_node.state_id if previous_node else None,
            to_state_id="",  # filled in below, once state_id itself is known.
            observation_confidence=observation_confidence,
            interpretation_confidence=interpretation_confidence,
            evidence=transition_evidence,
        )

    provisional = MarketStateNode(
        state_id="", timestamp=snapshot.as_of_time.isoformat(), regime=regime, phenomena=tuple(p.phenomenon_type for p in phenomena_assessment.phenomena),
        volatility_state=volatility_state, liquidity_state=liquidity_state, event_state=event_state,
        decision_posture=decision_posture, evidence_bundle=evidence_bundle,
        previous_state_id=previous_node.state_id if previous_node else None,
        transition=transition,
    )
    from bujji.replay_engine.engine import fingerprint_state
    state_id = fingerprint_state(provisional.fingerprint_payload())
    if transition is not None:
        transition = replace(transition, to_state_id=state_id)
    return replace(provisional, state_id=state_id, transition=transition)
