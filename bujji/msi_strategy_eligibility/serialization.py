"""Deterministic JSON round-trip for StrategyEligibilityAssessment,
mirroring `bujji.msi_consensus.serialization`'s exact conventions
(explicit key ordering, no uuid4, no wall clock).
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import Contradiction, Explanation, StrategyEligibilityAssessment


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
        "why_eligible": list(e.why_eligible),
        "why_ineligible": list(e.why_ineligible),
        "supporting_evidence": list(e.supporting_evidence),
        "weakening_evidence": list(e.weakening_evidence),
        "what_would_change_it": list(e.what_would_change_it),
        "schema_version": e.schema_version,
    }


def explanation_from_dict(d: Dict[str, Any]) -> Explanation:
    return Explanation(
        assessment_id=d["assessment_id"],
        why_eligible=tuple(d["why_eligible"]),
        why_ineligible=tuple(d["why_ineligible"]),
        supporting_evidence=tuple(d["supporting_evidence"]),
        weakening_evidence=tuple(d["weakening_evidence"]),
        what_would_change_it=tuple(d["what_would_change_it"]),
        schema_version=d["schema_version"],
    )


def assessment_to_dict(a: StrategyEligibilityAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id,
        "timestamp": a.timestamp,
        "eligible_strategy_families": list(a.eligible_strategy_families),
        "ineligible_strategy_families": list(a.ineligible_strategy_families),
        "eligibility_confidence": a.eligibility_confidence,
        "supporting_assessment_ids": list(a.supporting_assessment_ids),
        "contradictions": [contradiction_to_dict(c) for c in a.contradictions],
        "explanation": explanation_to_dict(a.explanation),
        "provenance": a.provenance,
        "schema_version": a.schema_version,
    }


def assessment_from_dict(d: Dict[str, Any]) -> StrategyEligibilityAssessment:
    return StrategyEligibilityAssessment(
        assessment_id=d["assessment_id"],
        timestamp=d["timestamp"],
        eligible_strategy_families=tuple(d["eligible_strategy_families"]),
        ineligible_strategy_families=tuple(d["ineligible_strategy_families"]),
        eligibility_confidence=d["eligibility_confidence"],
        supporting_assessment_ids=tuple(d["supporting_assessment_ids"]),
        contradictions=tuple(contradiction_from_dict(c) for c in d["contradictions"]),
        explanation=explanation_from_dict(d["explanation"]),
        provenance=d["provenance"],
        schema_version=d["schema_version"],
    )


def assessment_to_json(a: StrategyEligibilityAssessment) -> str:
    return json.dumps(assessment_to_dict(a), sort_keys=True, default=repr)


def assessment_from_json(text: str) -> StrategyEligibilityAssessment:
    return assessment_from_dict(json.loads(text))
