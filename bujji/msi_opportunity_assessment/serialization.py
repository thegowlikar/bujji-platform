"""OAE serialization — Series 104. Pure dict/JSON round-trip, mirrors
every prior MSI package's convention."""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import OpportunityAssessment, OpportunityAssessmentExplanation


def explanation_to_dict(e: OpportunityAssessmentExplanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id, "why_this_classification": list(e.why_this_classification),
        "why_not_alternatives": list(e.why_not_alternatives),
        "opportunity_criteria_checked": list(e.opportunity_criteria_checked), "schema_version": e.schema_version,
    }


def explanation_from_dict(d: Dict[str, Any]) -> OpportunityAssessmentExplanation:
    return OpportunityAssessmentExplanation(
        assessment_id=d["assessment_id"], why_this_classification=tuple(d["why_this_classification"]),
        why_not_alternatives=tuple(d["why_not_alternatives"]),
        opportunity_criteria_checked=tuple(d["opportunity_criteria_checked"]), schema_version=d["schema_version"],
    )


def assessment_to_dict(a: OpportunityAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id, "day": a.day, "timestamp": a.timestamp,
        "classification": a.classification, "earliest_causal_timestamp": a.earliest_causal_timestamp,
        "supporting_market_phenomena": list(a.supporting_market_phenomena),
        "supporting_counterfactual_session": a.supporting_counterfactual_session,
        "supporting_evidence_packets": list(a.supporting_evidence_packets),
        "supporting_decision_records": list(a.supporting_decision_records),
        "supporting_reasoning": list(a.supporting_reasoning), "evidence_strength": a.evidence_strength,
        "replay_references": list(a.replay_references), "assumptions": list(a.assumptions),
        "explanation": explanation_to_dict(a.explanation), "provenance": a.provenance,
        "schema_version": a.schema_version,
    }


def assessment_from_dict(d: Dict[str, Any]) -> OpportunityAssessment:
    return OpportunityAssessment(
        assessment_id=d["assessment_id"], day=d["day"], timestamp=d["timestamp"],
        classification=d["classification"], earliest_causal_timestamp=d["earliest_causal_timestamp"],
        supporting_market_phenomena=tuple(d["supporting_market_phenomena"]),
        supporting_counterfactual_session=d["supporting_counterfactual_session"],
        supporting_evidence_packets=tuple(d["supporting_evidence_packets"]),
        supporting_decision_records=tuple(d["supporting_decision_records"]),
        supporting_reasoning=tuple(d["supporting_reasoning"]), evidence_strength=d["evidence_strength"],
        replay_references=tuple(d["replay_references"]), assumptions=tuple(d["assumptions"]),
        explanation=explanation_from_dict(d["explanation"]), provenance=d["provenance"],
        schema_version=d["schema_version"],
    )


def assessment_to_json(a: OpportunityAssessment) -> str:
    return json.dumps(assessment_to_dict(a), sort_keys=True, default=repr)


def assessment_from_json(text: str) -> OpportunityAssessment:
    return assessment_from_dict(json.loads(text))
