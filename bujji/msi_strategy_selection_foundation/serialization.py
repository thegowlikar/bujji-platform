"""Deterministic JSON round-trip for StrategySuitabilityAssessment."""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import Explanation, StrategySuitabilityAssessment


def explanation_to_dict(e: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id,
        "why_suitable": list(e.why_suitable),
        "why_unsuitable": list(e.why_unsuitable),
        "supporting_evidence": list(e.supporting_evidence),
        "rejecting_evidence": list(e.rejecting_evidence),
        "missing_evidence": list(e.missing_evidence),
        "schema_version": e.schema_version,
    }


def explanation_from_dict(d: Dict[str, Any]) -> Explanation:
    return Explanation(
        assessment_id=d["assessment_id"], why_suitable=tuple(d["why_suitable"]),
        why_unsuitable=tuple(d["why_unsuitable"]), supporting_evidence=tuple(d["supporting_evidence"]),
        rejecting_evidence=tuple(d["rejecting_evidence"]), missing_evidence=tuple(d["missing_evidence"]),
        schema_version=d["schema_version"],
    )


def assessment_to_dict(a: StrategySuitabilityAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id, "timestamp": a.timestamp, "strategy_family": a.strategy_family,
        "suitability": a.suitability, "supporting_reasons": list(a.supporting_reasons),
        "rejecting_reasons": list(a.rejecting_reasons), "required_missing_evidence": list(a.required_missing_evidence),
        "confidence": a.confidence, "supporting_assessment_ids": list(a.supporting_assessment_ids),
        "explanation": explanation_to_dict(a.explanation), "provenance": a.provenance, "schema_version": a.schema_version,
    }


def assessment_from_dict(d: Dict[str, Any]) -> StrategySuitabilityAssessment:
    return StrategySuitabilityAssessment(
        assessment_id=d["assessment_id"], timestamp=d["timestamp"], strategy_family=d["strategy_family"],
        suitability=d["suitability"], supporting_reasons=tuple(d["supporting_reasons"]),
        rejecting_reasons=tuple(d["rejecting_reasons"]), required_missing_evidence=tuple(d["required_missing_evidence"]),
        confidence=d["confidence"], supporting_assessment_ids=tuple(d["supporting_assessment_ids"]),
        explanation=explanation_from_dict(d["explanation"]), provenance=d["provenance"], schema_version=d["schema_version"],
    )


def assessment_to_json(a: StrategySuitabilityAssessment) -> str:
    return json.dumps(assessment_to_dict(a), sort_keys=True, default=repr)


def assessment_from_json(text: str) -> StrategySuitabilityAssessment:
    return assessment_from_dict(json.loads(text))
