"""MPC serialization — Series 103. Pure dict/JSON round-trip, mirrors
every prior MSI package's convention."""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import MarketPhenomenaReport, Phenomenon


def phenomenon_to_dict(p: Phenomenon) -> Dict[str, Any]:
    return {
        "phenomenon_id": p.phenomenon_id, "phenomenon_type": p.phenomenon_type, "day": p.day,
        "earliest_detection_timestamp": p.earliest_detection_timestamp,
        "latest_confirmation_timestamp": p.latest_confirmation_timestamp,
        "supporting_observations": list(p.supporting_observations),
        "evidence_references": list(p.evidence_references), "confidence": p.confidence,
        "duration_seconds": p.duration_seconds, "affected_instruments": list(p.affected_instruments),
        "schema_version": p.schema_version,
    }


def phenomenon_from_dict(d: Dict[str, Any]) -> Phenomenon:
    return Phenomenon(
        phenomenon_id=d["phenomenon_id"], phenomenon_type=d["phenomenon_type"], day=d["day"],
        earliest_detection_timestamp=d["earliest_detection_timestamp"],
        latest_confirmation_timestamp=d["latest_confirmation_timestamp"],
        supporting_observations=tuple(d["supporting_observations"]),
        evidence_references=tuple(d["evidence_references"]), confidence=d["confidence"],
        duration_seconds=d["duration_seconds"], affected_instruments=tuple(d["affected_instruments"]),
        schema_version=d["schema_version"],
    )


def report_to_dict(r: MarketPhenomenaReport) -> Dict[str, Any]:
    return {
        "report_id": r.report_id, "day": r.day, "generated_timestamp": r.generated_timestamp,
        "phenomena": [phenomenon_to_dict(p) for p in r.phenomena],
        "not_classifiable": list(r.not_classifiable), "not_classifiable_reason": r.not_classifiable_reason,
        "schema_version": r.schema_version,
    }


def report_from_dict(d: Dict[str, Any]) -> MarketPhenomenaReport:
    return MarketPhenomenaReport(
        report_id=d["report_id"], day=d["day"], generated_timestamp=d["generated_timestamp"],
        phenomena=tuple(phenomenon_from_dict(p) for p in d["phenomena"]),
        not_classifiable=tuple(d["not_classifiable"]), not_classifiable_reason=d["not_classifiable_reason"],
        schema_version=d["schema_version"],
    )


def report_to_json(r: MarketPhenomenaReport) -> str:
    return json.dumps(report_to_dict(r), sort_keys=True, default=repr)


def report_from_json(text: str) -> MarketPhenomenaReport:
    return report_from_dict(json.loads(text))
