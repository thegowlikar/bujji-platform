"""Deterministic JSON round-trip for MarketParticipantPositioningAssessment,
mirroring bujji.msi_market_direction.serialization's conventions."""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import Explanation, LensOpinion, MarketParticipantPositioningAssessment


def lens_opinion_to_dict(lo: LensOpinion) -> Dict[str, Any]:
    return {
        "lens_name": lo.lens_name,
        "positioning_lean": lo.positioning_lean,
        "confidence": lo.confidence,
        "supporting_evidence_ids": list(lo.supporting_evidence_ids),
        "reasoning": lo.reasoning,
    }


def lens_opinion_from_dict(d: Dict[str, Any]) -> LensOpinion:
    return LensOpinion(
        lens_name=d["lens_name"], positioning_lean=d["positioning_lean"], confidence=d["confidence"],
        supporting_evidence_ids=tuple(d["supporting_evidence_ids"]), reasoning=d["reasoning"],
    )


def explanation_to_dict(e: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id,
        "which_lenses_participated": list(e.which_lenses_participated),
        "which_bullish": list(e.which_bullish),
        "which_bearish": list(e.which_bearish),
        "which_neutral_or_unknown": list(e.which_neutral_or_unknown),
        "per_lens_evidence": list(e.per_lens_evidence),
        "missing_evidence": list(e.missing_evidence),
        "why_positioning_was_chosen": e.why_positioning_was_chosen,
        "schema_version": e.schema_version,
    }


def explanation_from_dict(d: Dict[str, Any]) -> Explanation:
    return Explanation(
        assessment_id=d["assessment_id"],
        which_lenses_participated=tuple(d["which_lenses_participated"]),
        which_bullish=tuple(d["which_bullish"]),
        which_bearish=tuple(d["which_bearish"]),
        which_neutral_or_unknown=tuple(d["which_neutral_or_unknown"]),
        per_lens_evidence=tuple(d["per_lens_evidence"]),
        missing_evidence=tuple(d["missing_evidence"]),
        why_positioning_was_chosen=d["why_positioning_was_chosen"],
        schema_version=d["schema_version"],
    )


def assessment_to_dict(a: MarketParticipantPositioningAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id,
        "timestamp": a.timestamp,
        "positioning_bias": a.positioning_bias,
        "positioning_strength": a.positioning_strength,
        "participating_lenses": [lens_opinion_to_dict(lo) for lo in a.participating_lenses],
        "conflicting_lenses": list(a.conflicting_lenses),
        "supporting_observation_ids": list(a.supporting_observation_ids),
        "explanation": explanation_to_dict(a.explanation),
        "provenance": a.provenance,
        "schema_version": a.schema_version,
    }


def assessment_from_dict(d: Dict[str, Any]) -> MarketParticipantPositioningAssessment:
    return MarketParticipantPositioningAssessment(
        assessment_id=d["assessment_id"],
        timestamp=d["timestamp"],
        positioning_bias=d["positioning_bias"],
        positioning_strength=d["positioning_strength"],
        participating_lenses=tuple(lens_opinion_from_dict(x) for x in d["participating_lenses"]),
        conflicting_lenses=tuple(d["conflicting_lenses"]),
        supporting_observation_ids=tuple(d["supporting_observation_ids"]),
        explanation=explanation_from_dict(d["explanation"]),
        provenance=d["provenance"],
        schema_version=d["schema_version"],
    )


def assessment_to_json(a: MarketParticipantPositioningAssessment) -> str:
    return json.dumps(assessment_to_dict(a), sort_keys=True, default=repr)


def assessment_from_json(text: str) -> MarketParticipantPositioningAssessment:
    return assessment_from_dict(json.loads(text))
