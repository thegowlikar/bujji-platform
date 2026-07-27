"""IntelligenceEvaluation <-> JSON serialization — BUJJI Options OS,
Integration Series 3, Sprint 1. Independent schema -- explicit,
hand-written, versioned, round-trip deterministic.
"""
from __future__ import annotations

from datetime import datetime

from .metrics import EvaluationProvenance
from .policy import IntelligenceEvaluation

SCHEMA_VERSION = "1.0.0"


def intelligence_evaluation_to_dict(evaluation: IntelligenceEvaluation) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": evaluation.evaluation_id,
        "decision_id": evaluation.decision_id,
        "timestamp": evaluation.timestamp.isoformat(),
        "outcome": evaluation.outcome,
        "reason": evaluation.reason,
        "production_direction": evaluation.production_direction,
        "snapshot_available": evaluation.snapshot_available,
        "snapshot_id": evaluation.snapshot_id,
        "provenance": {
            "source": evaluation.provenance.source,
            "ruleset_version": evaluation.provenance.ruleset_version,
        },
    }


def intelligence_evaluation_from_dict(data: dict) -> IntelligenceEvaluation:
    prov = data["provenance"]
    return IntelligenceEvaluation(
        evaluation_id=data["evaluation_id"],
        decision_id=data["decision_id"],
        timestamp=datetime.fromisoformat(data["timestamp"]),
        outcome=data["outcome"],
        reason=data["reason"],
        production_direction=data["production_direction"],
        snapshot_available=data["snapshot_available"],
        snapshot_id=data["snapshot_id"],
        provenance=EvaluationProvenance(source=prov["source"], ruleset_version=prov["ruleset_version"]),
    )
