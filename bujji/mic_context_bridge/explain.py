"""Phase 20.23 -- human-readable rendering. No new logic; reads fields
already computed by `adapter.py`.
"""
from __future__ import annotations

from .models import MarketUnderstandingContext


def explain_market_understanding_context(context: MarketUnderstandingContext) -> str:
    lines = [f"Market state: {context.market_state}"]
    if context.supporting_factors:
        lines.append("Supporting factors:")
        lines.extend(f"  - {f}" for f in context.supporting_factors)
    if context.conflicts:
        lines.append("Conflicts:")
        lines.extend(f"  - {c}" for c in context.conflicts)
    if context.uncertainties:
        lines.append("Uncertainties:")
        lines.extend(f"  - {u}" for u in context.uncertainties)
    lines.append(f"Explanation: {context.explanation}")
    return "\n".join(lines)
