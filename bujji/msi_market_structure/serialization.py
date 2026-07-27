"""Deterministic JSON round-trip for MarketStructureAssessment, mirroring
`bujji.msi_price_structure.serialization`'s exact conventions (explicit
key ordering, no uuid4, no wall clock).
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import Contradiction, Explanation, MarketStructureAssessment


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


def assessment_to_dict(a: MarketStructureAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id,
        "timestamp": a.timestamp,
        "structure_location": a.structure_location,
        "support_state": a.support_state,
        "resistance_state": a.resistance_state,
        "breakout_state": a.breakout_state,
        "breakdown_state": a.breakdown_state,
        "retest_state": a.retest_state,
        "rejection_state": a.rejection_state,
        "structural_balance": a.structural_balance,
        "confidence": a.confidence,
        "supporting_episode_ids": list(a.supporting_episode_ids),
        "supporting_event_ids": list(a.supporting_event_ids),
        "supporting_observation_ids": list(a.supporting_observation_ids),
        "contradictions": [contradiction_to_dict(c) for c in a.contradictions],
        "explanation": explanation_to_dict(a.explanation),
        "provenance": a.provenance,
        "schema_version": a.schema_version,
    }


def assessment_from_dict(d: Dict[str, Any]) -> MarketStructureAssessment:
    return MarketStructureAssessment(
        assessment_id=d["assessment_id"],
        timestamp=d["timestamp"],
        structure_location=d["structure_location"],
        support_state=d["support_state"],
        resistance_state=d["resistance_state"],
        breakout_state=d["breakout_state"],
        breakdown_state=d["breakdown_state"],
        retest_state=d["retest_state"],
        rejection_state=d["rejection_state"],
        structural_balance=d["structural_balance"],
        confidence=d["confidence"],
        supporting_episode_ids=tuple(d["supporting_episode_ids"]),
        supporting_event_ids=tuple(d["supporting_event_ids"]),
        supporting_observation_ids=tuple(d["supporting_observation_ids"]),
        contradictions=tuple(contradiction_from_dict(c) for c in d["contradictions"]),
        explanation=explanation_from_dict(d["explanation"]),
        provenance=d["provenance"],
        schema_version=d["schema_version"],
    )


def assessment_to_json(a: MarketStructureAssessment) -> str:
    return json.dumps(assessment_to_dict(a), sort_keys=True, default=repr)


def assessment_from_json(text: str) -> MarketStructureAssessment:
    return assessment_from_dict(json.loads(text))
