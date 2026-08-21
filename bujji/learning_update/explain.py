"""Phase 20.21 -- human-readable rendering. No new logic; reads fields
already computed by `evaluator.py`.
"""
from __future__ import annotations

from .models import LearningUpdateRecord


def explain_learning_update(record: LearningUpdateRecord) -> str:
    lines = [
        f"Strategy: {record.strategy_family} | update_id={record.update_id} | "
        f"source_shadow_result_id={record.source_shadow_result_id}",
        f"Decision: {record.decision_state}. Risk: {record.risk_context_state}. "
        f"Execution: {record.execution_outcome}. Reconciliation: {record.reconciliation_state}.",
        f"Learning classification: {record.learning_classification}.",
    ]
    if record.market_context_signature is not None:
        lines.append(f"Market context: {record.market_context_signature}.")
    return "\n".join(lines)
