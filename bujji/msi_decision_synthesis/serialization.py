"""Deterministic JSON round-trip for DomainSignal, MarketOpportunityAssessment,
and Explanation, reusing MOC v1/LMEE/MEE's serialization conventions
(explicit key ordering, no uuid4, no wall clock).
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import DomainSignal, Explanation, MarketOpportunityAssessment


def domain_signal_to_dict(signal: DomainSignal) -> Dict[str, Any]:
    return {
        "domain_name": signal.domain_name,
        "state": signal.state,
        "confidence": signal.confidence,
        "evidence_ids": list(signal.evidence_ids),
    }


def domain_signal_from_dict(d: Dict[str, Any]) -> DomainSignal:
    return DomainSignal(
        domain_name=d["domain_name"],
        state=d["state"],
        confidence=d["confidence"],
        evidence_ids=tuple(d.get("evidence_ids", [])),
    )


def assessment_to_dict(assessment: MarketOpportunityAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": assessment.assessment_id,
        "timestamp": assessment.timestamp,
        "opportunity_state": assessment.opportunity_state,
        "confidence_level": assessment.confidence_level,
        "opportunity_quality": assessment.opportunity_quality,
        "supporting_domains": list(assessment.supporting_domains),
        "conflicting_domains": list(assessment.conflicting_domains),
        "compatible_strategy_families": list(assessment.compatible_strategy_families),
        "incompatible_strategy_families": list(assessment.incompatible_strategy_families),
        "evidence_ids": list(assessment.evidence_ids),
        "episode_ids": list(assessment.episode_ids),
        "provenance": assessment.provenance,
        "schema_version": assessment.schema_version,
    }


def assessment_from_dict(d: Dict[str, Any]) -> MarketOpportunityAssessment:
    return MarketOpportunityAssessment(
        assessment_id=d["assessment_id"],
        timestamp=d["timestamp"],
        opportunity_state=d["opportunity_state"],
        confidence_level=d["confidence_level"],
        opportunity_quality=d["opportunity_quality"],
        supporting_domains=tuple(d["supporting_domains"]),
        conflicting_domains=tuple(d["conflicting_domains"]),
        compatible_strategy_families=tuple(d["compatible_strategy_families"]),
        incompatible_strategy_families=tuple(d["incompatible_strategy_families"]),
        evidence_ids=tuple(d["evidence_ids"]),
        episode_ids=tuple(d["episode_ids"]),
        provenance=d["provenance"],
        schema_version=d["schema_version"],
    )


def assessment_to_json(assessment: MarketOpportunityAssessment) -> str:
    return json.dumps(assessment_to_dict(assessment), sort_keys=True, default=repr)


def assessment_from_json(text: str) -> MarketOpportunityAssessment:
    return assessment_from_dict(json.loads(text))


def explanation_to_dict(explanation: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": explanation.assessment_id,
        "why": explanation.why,
        "why_not": list(explanation.why_not),
        "what_changed": explanation.what_changed,
        "domains_agreeing": list(explanation.domains_agreeing),
        "domains_disagreeing": list(explanation.domains_disagreeing),
        "missing_evidence": list(explanation.missing_evidence),
        "evidence_that_would_increase_confidence": list(explanation.evidence_that_would_increase_confidence),
        "schema_version": explanation.schema_version,
    }


def explanation_from_dict(d: Dict[str, Any]) -> Explanation:
    return Explanation(
        assessment_id=d["assessment_id"],
        why=d["why"],
        why_not=tuple(d["why_not"]),
        what_changed=d.get("what_changed"),
        domains_agreeing=tuple(d["domains_agreeing"]),
        domains_disagreeing=tuple(d["domains_disagreeing"]),
        missing_evidence=tuple(d["missing_evidence"]),
        evidence_that_would_increase_confidence=tuple(d["evidence_that_would_increase_confidence"]),
        schema_version=d["schema_version"],
    )


def explanation_to_json(explanation: Explanation) -> str:
    return json.dumps(explanation_to_dict(explanation), sort_keys=True, default=repr)


def explanation_from_json(text: str) -> Explanation:
    return explanation_from_dict(json.loads(text))
