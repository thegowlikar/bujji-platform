"""Deterministic JSON round-trip for VolatilityStructureAssessment."""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import Explanation, VolatilityStructureAssessment


def explanation_to_dict(e: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id, "why": list(e.why),
        "missing_evidence": list(e.missing_evidence),
        "would_increase_confidence": list(e.would_increase_confidence),
        "schema_version": e.schema_version,
    }


def explanation_from_dict(d: Dict[str, Any]) -> Explanation:
    return Explanation(
        assessment_id=d["assessment_id"], why=tuple(d["why"]),
        missing_evidence=tuple(d["missing_evidence"]),
        would_increase_confidence=tuple(d["would_increase_confidence"]),
        schema_version=d["schema_version"],
    )


def assessment_to_dict(a: VolatilityStructureAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id, "timestamp": a.timestamp,
        "volatility_regime": a.volatility_regime, "iv_state": a.iv_state,
        "expected_move_state": a.expected_move_state, "skew_state": a.skew_state,
        "term_structure_state": a.term_structure_state, "expansion_state": a.expansion_state,
        "compression_state": a.compression_state, "confidence": a.confidence,
        "iv_average": a.iv_average, "realized_vol": a.realized_vol,
        "expected_move_pct": a.expected_move_pct,
        "explanation": explanation_to_dict(a.explanation),
        "provenance": a.provenance, "schema_version": a.schema_version,
    }


def assessment_from_dict(d: Dict[str, Any]) -> VolatilityStructureAssessment:
    return VolatilityStructureAssessment(
        assessment_id=d["assessment_id"], timestamp=d["timestamp"],
        volatility_regime=d["volatility_regime"], iv_state=d["iv_state"],
        expected_move_state=d["expected_move_state"], skew_state=d["skew_state"],
        term_structure_state=d["term_structure_state"], expansion_state=d["expansion_state"],
        compression_state=d["compression_state"], confidence=d["confidence"],
        iv_average=d["iv_average"], realized_vol=d["realized_vol"],
        expected_move_pct=d["expected_move_pct"],
        explanation=explanation_from_dict(d["explanation"]),
        provenance=d["provenance"], schema_version=d["schema_version"],
    )


def assessment_to_json(a: VolatilityStructureAssessment) -> str:
    return json.dumps(assessment_to_dict(a), sort_keys=True, default=repr)


def assessment_from_json(text: str) -> VolatilityStructureAssessment:
    return assessment_from_dict(json.loads(text))
