"""Phase 20.9 -- explainability. Every portfolio decision must answer
"why" -- built from already-real fields (`AllocationAssessment`/
`OpportunityConflict`), never inventing a justification `portfolio.py`
didn't actually use.
"""
from __future__ import annotations

from .models import PortfolioDecision


def explain_portfolio_decision(decision: PortfolioDecision) -> str:
    lines = [f"Portfolio Decision: {decision.decision}"]
    if decision.primary:
        lines.append(f"Primary: {decision.primary}")
    if decision.kept:
        lines.append(f"Kept: {', '.join(decision.kept)}")
    if decision.excluded:
        lines.append(f"Excluded: {', '.join(decision.excluded)}")
    lines.append("Reasons:")
    for reason in decision.reasons:
        lines.append(f"  + {reason}")
    if decision.conflicts:
        lines.append("Conflicts evaluated:")
        for c in decision.conflicts:
            lines.append(f"  {c.strategy_a} vs {c.strategy_b}: {c.conflict_state} -- {c.detail}")
    return "\n".join(lines)
