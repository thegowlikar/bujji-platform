"""build_decision_context() -- Phase 19.4.

Composes a `DecisionContext` from a `MarketIntelligenceSnapshot` via
`StrategyCompatibilityAssessment`. Purely a function of its inputs --
deterministic by construction, since both the snapshot and the
compatibility engine are themselves deterministic (Phase 19.3 / this
phase's own `compatibility_engine.py`).
"""
from __future__ import annotations

from bujji.intelligence.market_intelligence_snapshot.models import MarketIntelligenceSnapshot

from .compatibility_engine import assess_strategy_compatibility
from .models import DecisionContext


def build_decision_context(snapshot: MarketIntelligenceSnapshot) -> DecisionContext:
    assessment = assess_strategy_compatibility(snapshot)

    reasoning_parts = [f"posture: {snapshot.posture.value}", snapshot.thesis.primary_thesis]
    if assessment.compatible_strategy_families:
        reasoning_parts.append(f"compatible: {', '.join(assessment.compatible_strategy_families)}")
    if assessment.incompatible_strategy_families:
        reasoning_parts.append(f"blocked: {', '.join(assessment.incompatible_strategy_families)}")
    reasoning = "; ".join(reasoning_parts)

    evidence = assessment.supporting_evidence + assessment.blocking_evidence

    return DecisionContext(
        intelligence_snapshot_reference=snapshot.intelligence_snapshot_id,
        compatible_strategy_families=assessment.compatible_strategy_families,
        blocked_strategy_families=assessment.incompatible_strategy_families,
        confidence=snapshot.evidence_bundle.confidence,
        reasoning=reasoning,
        evidence=evidence,
    )
