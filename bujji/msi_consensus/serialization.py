"""Deterministic JSON round-trip for ConsensusAssessment, mirroring
`bujji.msi_market_structure.serialization`'s exact conventions
(explicit key ordering, no uuid4, no wall clock).
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import Contradiction, ConsensusAssessment, Explanation


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
        "which_domains_agree": list(e.which_domains_agree),
        "which_domains_disagree": list(e.which_domains_disagree),
        "which_evidence_is_missing": list(e.which_evidence_is_missing),
        "why_consensus_is_high_or_low": e.why_consensus_is_high_or_low,
        "what_additional_domains_would_increase_confidence": list(e.what_additional_domains_would_increase_confidence),
        "what_changed": e.what_changed,
        "schema_version": e.schema_version,
    }


def explanation_from_dict(d: Dict[str, Any]) -> Explanation:
    return Explanation(
        assessment_id=d["assessment_id"],
        which_domains_agree=tuple(d["which_domains_agree"]),
        which_domains_disagree=tuple(d["which_domains_disagree"]),
        which_evidence_is_missing=tuple(d["which_evidence_is_missing"]),
        why_consensus_is_high_or_low=d["why_consensus_is_high_or_low"],
        what_additional_domains_would_increase_confidence=tuple(d["what_additional_domains_would_increase_confidence"]),
        what_changed=d.get("what_changed"),
        schema_version=d["schema_version"],
    )


def assessment_to_dict(a: ConsensusAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id,
        "timestamp": a.timestamp,
        "participating_domains": list(a.participating_domains),
        "agreeing_domains": list(a.agreeing_domains),
        "conflicting_domains": list(a.conflicting_domains),
        "missing_domains": list(a.missing_domains),
        "consensus_level": a.consensus_level,
        "evidence_sufficiency": a.evidence_sufficiency,
        "contradiction_density": a.contradiction_density,
        "confidence_calibration": a.confidence_calibration,
        "supporting_assessment_ids": list(a.supporting_assessment_ids),
        "explanation": explanation_to_dict(a.explanation),
        "provenance": a.provenance,
        "schema_version": a.schema_version,
    }


def assessment_from_dict(d: Dict[str, Any]) -> ConsensusAssessment:
    return ConsensusAssessment(
        assessment_id=d["assessment_id"],
        timestamp=d["timestamp"],
        participating_domains=tuple(d["participating_domains"]),
        agreeing_domains=tuple(d["agreeing_domains"]),
        conflicting_domains=tuple(d["conflicting_domains"]),
        missing_domains=tuple(d["missing_domains"]),
        consensus_level=d["consensus_level"],
        evidence_sufficiency=d["evidence_sufficiency"],
        contradiction_density=d["contradiction_density"],
        confidence_calibration=d["confidence_calibration"],
        supporting_assessment_ids=tuple(d["supporting_assessment_ids"]),
        explanation=explanation_from_dict(d["explanation"]),
        provenance=d["provenance"],
        schema_version=d["schema_version"],
    )


def assessment_to_json(a: ConsensusAssessment) -> str:
    return json.dumps(assessment_to_dict(a), sort_keys=True, default=repr)


def assessment_from_json(text: str) -> ConsensusAssessment:
    return assessment_from_dict(json.loads(text))
