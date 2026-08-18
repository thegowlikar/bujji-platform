"""Phase 20.17 -- human-readable rendering of a `RiskGovernorAssessment`.
No new logic; reads fields already computed by `bridge.py`.
"""
from __future__ import annotations

from .models import STATUS_ADMITTED, STATUS_BLOCKED, RiskGovernorAssessment


def explain_risk_governor_assessment(assessment: RiskGovernorAssessment) -> str:
    lines = [f"Strategy: {assessment.strategy_name}", f"Risk governor status: {assessment.final_status}"]

    if assessment.final_status == STATUS_ADMITTED:
        lines.append(
            f"Admitted through D.1-D.3 (capital safety, portfolio risk, risk budget). "
            f"Real risk ceiling: {assessment.real_risk_ceiling_units} unit(s)."
        )
    elif assessment.final_status == STATUS_BLOCKED:
        lines.append(f"Blocked at {assessment.blocking_stage}.")

    if assessment.capital_status is not None:
        lines.append(f"D.1 Capital safety: {assessment.capital_status} -- {assessment.capital_explanation}")
    if assessment.portfolio_status is not None:
        lines.append(f"D.2 Portfolio risk: {assessment.portfolio_status} -- {assessment.portfolio_explanation}")
    if assessment.budget_status is not None:
        lines.append(f"D.3 Risk budget: {assessment.budget_status} -- {assessment.budget_explanation}")

    lines.append(assessment.probe_disclosure)
    lines.append(f"Skipped stages: {', '.join(assessment.skipped_stages)} -- {assessment.skip_reason}")
    return "\n".join(lines)
