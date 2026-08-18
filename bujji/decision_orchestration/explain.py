"""Phase 20.10 -- explainability. Every final decision must answer:
what did Bujji decide, why, what evidence supported it, what blocked
it, what uncertainty remains -- built entirely from `FinalDecision`'s
own already-real fields, never inventing a justification `engine.py`
didn't actually use.
"""
from __future__ import annotations

from .models import FinalDecision


def explain_decision(decision: FinalDecision) -> str:
    lines = [
        f"Decision: {decision.decision_state}",
        f"Candidate: {decision.strategy_name or 'UNKNOWN'}",
    ]
    if decision.positive:
        lines.append("Positive:")
        for p in decision.positive:
            lines.append(f"  + {p}")
    if decision.negative:
        lines.append("Negative:")
        for n in decision.negative:
            lines.append(f"  - {n}")
    if decision.unknown:
        lines.append("Unknown:")
        for u in decision.unknown:
            lines.append(f"  ? {u}")
    return "\n".join(lines)
