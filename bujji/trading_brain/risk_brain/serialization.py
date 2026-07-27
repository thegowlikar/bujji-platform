"""JSON round-trip for RiskAssessment."""
from __future__ import annotations

from typing import Any, Dict

from .models import RiskAssessment


def assessment_to_dict(a: RiskAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id,
        "status": a.status,
        "risk_level": a.risk_level,
        "approval": a.approval,
        "blocking_reason": a.blocking_reason,
        "warning_reasons": list(a.warning_reasons),
        "required_controls": list(a.required_controls),
        "confidence": a.confidence,
        "decision_trace": a.decision_trace,
        "strategy_decision_id": a.strategy_decision_id,
        "market_state_assessment_id": a.market_state_assessment_id,
        "timestamp": a.timestamp,
        "version": a.version,
    }


def assessment_from_dict(d: Dict[str, Any]) -> RiskAssessment:
    return RiskAssessment(
        assessment_id=d["assessment_id"],
        status=d["status"],
        risk_level=d["risk_level"],
        approval=d["approval"],
        blocking_reason=d.get("blocking_reason"),
        warning_reasons=tuple(d["warning_reasons"]),
        required_controls=tuple(d["required_controls"]),
        confidence=d["confidence"],
        decision_trace=d["decision_trace"],
        strategy_decision_id=d.get("strategy_decision_id"),
        market_state_assessment_id=d.get("market_state_assessment_id"),
        timestamp=d["timestamp"],
        version=d["version"],
    )
