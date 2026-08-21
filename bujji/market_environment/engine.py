"""build_market_environment_assessment() -- Phase 19.9.

The one entry point. Pure composition over already-built objects:
MarketStateNode (Phase 19.8), DecisionIntelligenceSnapshot (Phase 19.6,
which already carries phenomena-derived posture, contradictions, and
memory_context). Never calls a brain's `.analyze(`, never instantiates a
broker, never queries a datastore itself. No wall-clock call anywhere
in this module.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from bujji.decision_intelligence.models import DecisionIntelligenceSnapshot
from bujji.market_state_graph.models import MarketStateNode

from .classify import classify_environment
from .models import MarketEnvironmentAssessment


def build_market_environment_assessment(
    *,
    node: MarketStateNode,
    decision_intelligence: DecisionIntelligenceSnapshot,
    created_at: datetime,
) -> MarketEnvironmentAssessment:
    environment_type, supporting, blocking, confidence = classify_environment(node, decision_intelligence)

    evidence_bundle = {
        "market_state_evidence": node.evidence_bundle,
        "decision_evidence": decision_intelligence.evidence_bundle,
    }

    provisional = MarketEnvironmentAssessment(
        environment_id="", created_at=created_at, market_state_id=node.state_id,
        environment_type=environment_type, confidence=confidence,
        supporting_conditions=supporting, blocking_conditions=blocking,
        historical_similarity=decision_intelligence.memory_context.to_dict(),
        decision_posture=decision_intelligence.recommended_posture.value,
        evidence_bundle=evidence_bundle,
    )
    from bujji.replay_engine.engine import fingerprint_state
    environment_id = fingerprint_state(provisional.fingerprint_payload())
    return replace(provisional, environment_id=environment_id)
