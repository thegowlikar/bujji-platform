"""Deterministic JSON round-trip for PriceStructureAssessment, reusing
established conventions (explicit key ordering, no uuid4, no wall
clock) from `bujji.market_episode.serialization` /
`bujji.msi_decision_synthesis.serialization`.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import Contradiction, Explanation, PriceStructureAssessment


def contradiction_to_dict(c: Contradiction) -> Dict[str, Any]:
    return {
        "dimension_a": c.dimension_a, "value_a": c.value_a,
        "dimension_b": c.dimension_b, "value_b": c.value_b,
        "reason": c.reason,
    }


def contradiction_from_dict(d: Dict[str, Any]) -> Contradiction:
    return Contradiction(
        dimension_a=d["dimension_a"], value_a=d["value_a"],
        dimension_b=d["dimension_b"], value_b=d["value_b"],
        reason=d["reason"],
    )


def explanation_to_dict(e: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id,
        "what_changed": e.what_changed,
        "why": list(e.why),
        "which_episodes_caused_it": list(e.which_episodes_caused_it),
        "which_observations_support_it": list(e.which_observations_support_it),
        "missing_evidence": list(e.missing_evidence),
        "would_increase_confidence": list(e.would_increase_confidence),
        "schema_version": e.schema_version,
    }


def explanation_from_dict(d: Dict[str, Any]) -> Explanation:
    return Explanation(
        assessment_id=d["assessment_id"],
        what_changed=d.get("what_changed"),
        why=tuple(d["why"]),
        which_episodes_caused_it=tuple(d["which_episodes_caused_it"]),
        which_observations_support_it=tuple(d["which_observations_support_it"]),
        missing_evidence=tuple(d["missing_evidence"]),
        would_increase_confidence=tuple(d["would_increase_confidence"]),
        schema_version=d["schema_version"],
    )


def assessment_to_dict(a: PriceStructureAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id,
        "timestamp": a.timestamp,
        "structure_state": a.structure_state,
        "trend_state": a.trend_state,
        "swing_state": a.swing_state,
        "compression_state": a.compression_state,
        "expansion_state": a.expansion_state,
        "balance_state": a.balance_state,
        "structure_integrity": a.structure_integrity,
        "confidence": a.confidence,
        "supporting_episode_ids": list(a.supporting_episode_ids),
        "supporting_event_ids": list(a.supporting_event_ids),
        "supporting_observation_ids": list(a.supporting_observation_ids),
        "contradictions": [contradiction_to_dict(c) for c in a.contradictions],
        "explanation": explanation_to_dict(a.explanation),
        "trend_direction_signal": a.trend_direction_signal,
        "provenance": a.provenance,
        "schema_version": a.schema_version,
    }


def assessment_from_dict(d: Dict[str, Any]) -> PriceStructureAssessment:
    return PriceStructureAssessment(
        assessment_id=d["assessment_id"],
        timestamp=d["timestamp"],
        structure_state=d["structure_state"],
        trend_state=d["trend_state"],
        swing_state=d["swing_state"],
        compression_state=d["compression_state"],
        expansion_state=d["expansion_state"],
        balance_state=d["balance_state"],
        structure_integrity=d["structure_integrity"],
        confidence=d["confidence"],
        supporting_episode_ids=tuple(d["supporting_episode_ids"]),
        supporting_event_ids=tuple(d["supporting_event_ids"]),
        supporting_observation_ids=tuple(d["supporting_observation_ids"]),
        contradictions=tuple(contradiction_from_dict(c) for c in d["contradictions"]),
        explanation=explanation_from_dict(d["explanation"]),
        trend_direction_signal=d.get("trend_direction_signal"),
        provenance=d["provenance"],
        schema_version=d["schema_version"],
    )


def assessment_to_json(a: PriceStructureAssessment) -> str:
    return json.dumps(assessment_to_dict(a), sort_keys=True, default=repr)


def assessment_from_json(text: str) -> PriceStructureAssessment:
    return assessment_from_dict(json.loads(text))
