"""build_decision_intelligence_snapshot() -- Phase 19.6.

The one entry point. Pure composition over already-built objects from
every prior layer:

    MarketRealitySnapshot (referenced via snapshot.reality_fingerprint)
        -> MarketIntelligenceSnapshot (Phase 19.3, already built)
        -> DecisionContext (Phase 19.4, already built)
        -> MarketMemoryEntry matches (Phase 19.5, already queried)
        -> DecisionIntelligenceSnapshot (this phase)

This function never calls a brain's `.analyze(`, never instantiates a
broker, never queries an `EventStore`/database itself -- every input is
handed in by the caller, exactly like `build_market_intelligence_snapshot()`
never calls a brain and `build_decision_context()` never builds a
snapshot. No wall-clock call anywhere in this module.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import List, Tuple

from bujji.decision_context.compatibility_engine import assess_strategy_compatibility
from bujji.decision_context.models import DecisionContext
from bujji.intelligence.market_intelligence_snapshot.models import MarketIntelligenceSnapshot
from bujji.market_understanding.memory_models import MarketMemoryEntry
from bujji.market_understanding.memory_similarity import SimilarityExplanation
from bujji.replay_engine.engine import fingerprint_state

from .evidence import build_decision_evidence_bundle
from .models import DecisionIntelligenceSnapshot
from .reasoning import (
    build_memory_context,
    build_observations,
    build_strategy_family_assessment,
    derive_environment_state,
    derive_posture,
    detect_contradictions,
)


def build_decision_intelligence_snapshot(
    *,
    snapshot: MarketIntelligenceSnapshot,
    decision_context: DecisionContext,
    memory_matches: List[Tuple[MarketMemoryEntry, SimilarityExplanation]],
    created_at: datetime,
) -> DecisionIntelligenceSnapshot:
    assessment = assess_strategy_compatibility(snapshot)

    environment_assessment = derive_environment_state(snapshot)
    strategy_family_assessment = build_strategy_family_assessment(decision_context)
    memory_context = build_memory_context(memory_matches)
    risk_observations, opportunity_observations, uncertainty_observations = build_observations(assessment)
    contradictions = detect_contradictions(snapshot, assessment)
    recommended_posture = derive_posture(snapshot, assessment, contradictions)
    # invalidation_conditions carried forward verbatim from Phase 19.3's
    # own thesis -- never recomputed at this layer.
    invalidation_conditions = snapshot.invalidation_conditions

    matched_ids = tuple(entry.market_memory_id for entry, _ in memory_matches)
    evidence_bundle = build_decision_evidence_bundle(snapshot, matched_ids)

    # decision_context_id: a content fingerprint of the real DecisionContext
    # this snapshot reasoned from -- DecisionContext itself (Phase 19.4)
    # carries no stored id, so this is computed here rather than
    # retrofitting a new field onto that already-shipped, frozen model.
    decision_context_id = fingerprint_state(decision_context.to_dict())

    provisional = DecisionIntelligenceSnapshot(
        decision_intelligence_id="",
        created_at=created_at,
        market_intelligence_snapshot_id=snapshot.intelligence_snapshot_id,
        decision_context_id=decision_context_id,
        environment_assessment=environment_assessment,
        strategy_family_assessment=strategy_family_assessment,
        memory_context=memory_context,
        risk_observations=risk_observations,
        opportunity_observations=opportunity_observations,
        uncertainty_observations=uncertainty_observations,
        contradictions=contradictions,
        recommended_posture=recommended_posture,
        invalidation_conditions=invalidation_conditions,
        evidence_bundle=evidence_bundle.to_dict(),
    )
    decision_intelligence_id = fingerprint_state(provisional.fingerprint_payload())
    return replace(provisional, decision_intelligence_id=decision_intelligence_id)
