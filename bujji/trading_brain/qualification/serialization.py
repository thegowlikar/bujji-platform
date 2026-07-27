"""JSON round-trip for DecisionPipelineQualification."""
from __future__ import annotations

from typing import Any, Dict

from .models import DecisionPipelineQualification


def qualification_to_dict(q: DecisionPipelineQualification) -> Dict[str, Any]:
    return {
        "qualification_id": q.qualification_id,
        "pipeline_status": q.pipeline_status,
        "completed_stages": list(q.completed_stages),
        "failed_stage": q.failed_stage,
        "decision_fingerprint": q.decision_fingerprint,
        "deterministic": q.deterministic,
        "warnings": list(q.warnings),
        "validation_trace": q.validation_trace,
        "timestamp": q.timestamp,
        "version": q.version,
    }


def qualification_from_dict(d: Dict[str, Any]) -> DecisionPipelineQualification:
    return DecisionPipelineQualification(
        qualification_id=d["qualification_id"],
        pipeline_status=d["pipeline_status"],
        completed_stages=tuple(d["completed_stages"]),
        failed_stage=d.get("failed_stage"),
        decision_fingerprint=d["decision_fingerprint"],
        deterministic=d["deterministic"],
        warnings=tuple(d["warnings"]),
        validation_trace=d["validation_trace"],
        timestamp=d["timestamp"],
        version=d["version"],
    )
