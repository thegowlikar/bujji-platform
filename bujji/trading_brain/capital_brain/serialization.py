"""JSON round-trip for CapitalDecision."""
from __future__ import annotations

from typing import Any, Dict

from .models import CapitalDecision


def decision_to_dict(d: CapitalDecision) -> Dict[str, Any]:
    return {
        "decision_id": d.decision_id,
        "capital_intent": d.capital_intent,
        "allocation_status": d.allocation_status,
        "allocation_reason": d.allocation_reason,
        "allocation_constraints": list(d.allocation_constraints),
        "required_controls": list(d.required_controls),
        "confidence": d.confidence,
        "decision_trace": d.decision_trace,
        "risk_assessment_id": d.risk_assessment_id,
        "timestamp": d.timestamp,
        "version": d.version,
    }


def decision_from_dict(d: Dict[str, Any]) -> CapitalDecision:
    return CapitalDecision(
        decision_id=d["decision_id"],
        capital_intent=d["capital_intent"],
        allocation_status=d["allocation_status"],
        allocation_reason=d["allocation_reason"],
        allocation_constraints=tuple(d["allocation_constraints"]),
        required_controls=tuple(d["required_controls"]),
        confidence=d["confidence"],
        decision_trace=d["decision_trace"],
        risk_assessment_id=d.get("risk_assessment_id"),
        timestamp=d["timestamp"],
        version=d["version"],
    )
