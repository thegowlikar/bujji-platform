"""EEB serialization — Series 106. Pure dict/JSON round-trip, mirrors
every prior MSI package's convention."""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import EngineeringEvidenceExplanation, EngineeringEvidenceReport


def explanation_to_dict(e: EngineeringEvidenceExplanation) -> Dict[str, Any]:
    return {
        "report_id": e.report_id, "why_this_decision": list(e.why_this_decision),
        "criteria_evaluated": list(e.criteria_evaluated), "schema_version": e.schema_version,
    }


def explanation_from_dict(d: Dict[str, Any]) -> EngineeringEvidenceExplanation:
    return EngineeringEvidenceExplanation(
        report_id=d["report_id"], why_this_decision=tuple(d["why_this_decision"]),
        criteria_evaluated=tuple(d["criteria_evaluated"]), schema_version=d["schema_version"],
    )


def report_to_dict(r: EngineeringEvidenceReport) -> Dict[str, Any]:
    return {
        "report_id": r.report_id, "generated_timestamp": r.generated_timestamp,
        "hypothesis_label": r.hypothesis_label,
        "referenced_knowledge_validation_reports": list(r.referenced_knowledge_validation_reports),
        "referenced_evidence_packets": list(r.referenced_evidence_packets),
        "referenced_opportunity_assessments": list(r.referenced_opportunity_assessments),
        "referenced_counterfactual_sessions": list(r.referenced_counterfactual_sessions),
        "referenced_phenomena_reports": list(r.referenced_phenomena_reports),
        "referenced_decision_records": list(r.referenced_decision_records),
        "supporting_statistics": [list(t) for t in r.supporting_statistics],
        "supporting_reasoning": list(r.supporting_reasoning),
        "contradictory_observations": list(r.contradictory_observations),
        "known_limitations": list(r.known_limitations), "decision": r.decision,
        "explanation": explanation_to_dict(r.explanation), "provenance": r.provenance,
        "schema_version": r.schema_version,
    }


def report_from_dict(d: Dict[str, Any]) -> EngineeringEvidenceReport:
    return EngineeringEvidenceReport(
        report_id=d["report_id"], generated_timestamp=d["generated_timestamp"],
        hypothesis_label=d["hypothesis_label"],
        referenced_knowledge_validation_reports=tuple(d["referenced_knowledge_validation_reports"]),
        referenced_evidence_packets=tuple(d["referenced_evidence_packets"]),
        referenced_opportunity_assessments=tuple(d["referenced_opportunity_assessments"]),
        referenced_counterfactual_sessions=tuple(d["referenced_counterfactual_sessions"]),
        referenced_phenomena_reports=tuple(d["referenced_phenomena_reports"]),
        referenced_decision_records=tuple(d["referenced_decision_records"]),
        supporting_statistics=tuple(tuple(x) for x in d["supporting_statistics"]),
        supporting_reasoning=tuple(d["supporting_reasoning"]),
        contradictory_observations=tuple(d["contradictory_observations"]),
        known_limitations=tuple(d["known_limitations"]), decision=d["decision"],
        explanation=explanation_from_dict(d["explanation"]), provenance=d["provenance"],
        schema_version=d["schema_version"],
    )


def report_to_json(r: EngineeringEvidenceReport) -> str:
    return json.dumps(report_to_dict(r), sort_keys=True, default=repr)


def report_from_json(text: str) -> EngineeringEvidenceReport:
    return report_from_dict(json.loads(text))
