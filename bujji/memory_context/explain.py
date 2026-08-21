"""Phase 20.22 -- human-readable rendering. No new logic; reads fields
already computed by `adapter.py`.
"""
from __future__ import annotations

from .models import MemoryDecisionContext


def explain_memory_decision_context(context: MemoryDecisionContext) -> str:
    lines = [
        f"Strategy: {context.original_decision.strategy_name} | decision_status={context.decision_status}",
        context.memory_effect_summary,
        context.explanation,
    ]
    if context.adjusted_confidence_view is not None:
        lines.append(f"Memory-adjusted confidence view: {context.adjusted_confidence_view}")
    return "\n".join(lines)
