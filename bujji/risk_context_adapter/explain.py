"""Phase 20.17.1 -- human-readable rendering. No new logic; reads
fields already computed by `adapter.py`.
"""
from __future__ import annotations

from .models import RiskContextAssessment, RiskContextRequest


def explain_risk_context(request: RiskContextRequest, assessment: RiskContextAssessment) -> str:
    lines = [
        f"Strategy: {request.strategy_name} (opportunity_id={request.opportunity_id})",
        f"Decision state: {request.decision_state}",
        f"Risk context status: {assessment.status}",
        assessment.explanation,
    ]
    if assessment.governor_response is not None:
        lines.append(f"D.1 governor response: {assessment.governor_response}")
    if assessment.blockers:
        lines.append("Blockers/notes: " + "; ".join(assessment.blockers))
    return "\n".join(lines)
