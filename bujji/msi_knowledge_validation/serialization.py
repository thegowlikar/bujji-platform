"""KVE serialization — Series 105. Pure dict/JSON round-trip, mirrors
every prior MSI package's convention."""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import KnowledgeValidationExplanation, KnowledgeValidationReport


def explanation_to_dict(e: KnowledgeValidationExplanation) -> Dict[str, Any]:
    return {
        "validation_id": e.validation_id, "why_this_state": list(e.why_this_state),
        "metrics_considered": list(e.metrics_considered), "schema_version": e.schema_version,
    }


def explanation_from_dict(d: Dict[str, Any]) -> KnowledgeValidationExplanation:
    return KnowledgeValidationExplanation(
        validation_id=d["validation_id"], why_this_state=tuple(d["why_this_state"]),
        metrics_considered=tuple(d["metrics_considered"]), schema_version=d["schema_version"],
    )


def report_to_dict(r: KnowledgeValidationReport) -> Dict[str, Any]:
    return {
        "validation_id": r.validation_id, "hypothesis_label": r.hypothesis_label,
        "generated_timestamp": r.generated_timestamp, "occurrence_count": r.occurrence_count,
        "diversity_count": r.diversity_count, "consistency": r.consistency,
        "consistency_ratio": r.consistency_ratio, "replay_support_ratio": r.replay_support_ratio,
        "causal_validity_ratio": r.causal_validity_ratio, "evidence_growth": r.evidence_growth,
        "evidence_decay": r.evidence_decay, "validation_state": r.validation_state,
        "supporting_evidence_packets": list(r.supporting_evidence_packets),
        "supporting_opportunity_assessments": list(r.supporting_opportunity_assessments),
        "supporting_counterfactual_sessions": list(r.supporting_counterfactual_sessions),
        "supporting_phenomena": list(r.supporting_phenomena),
        "historical_occurrence_statistics": [list(t) for t in r.historical_occurrence_statistics],
        "explanation": explanation_to_dict(r.explanation), "provenance": r.provenance,
        "schema_version": r.schema_version,
    }


def report_from_dict(d: Dict[str, Any]) -> KnowledgeValidationReport:
    return KnowledgeValidationReport(
        validation_id=d["validation_id"], hypothesis_label=d["hypothesis_label"],
        generated_timestamp=d["generated_timestamp"], occurrence_count=d["occurrence_count"],
        diversity_count=d["diversity_count"], consistency=d["consistency"],
        consistency_ratio=d["consistency_ratio"], replay_support_ratio=d["replay_support_ratio"],
        causal_validity_ratio=d["causal_validity_ratio"], evidence_growth=d["evidence_growth"],
        evidence_decay=d["evidence_decay"], validation_state=d["validation_state"],
        supporting_evidence_packets=tuple(d["supporting_evidence_packets"]),
        supporting_opportunity_assessments=tuple(d["supporting_opportunity_assessments"]),
        supporting_counterfactual_sessions=tuple(d["supporting_counterfactual_sessions"]),
        supporting_phenomena=tuple(d["supporting_phenomena"]),
        historical_occurrence_statistics=tuple(tuple(x) for x in d["historical_occurrence_statistics"]),
        explanation=explanation_from_dict(d["explanation"]), provenance=d["provenance"],
        schema_version=d["schema_version"],
    )


def report_to_json(r: KnowledgeValidationReport) -> str:
    return json.dumps(report_to_dict(r), sort_keys=True, default=repr)


def report_from_json(text: str) -> KnowledgeValidationReport:
    return report_from_dict(json.loads(text))
