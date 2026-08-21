"""Phase 20.18 -- human-readable rendering. No new logic; reads fields
already computed by `planner.py`/`simulator.py`/`analytics.py`.
"""
from __future__ import annotations

from typing import Optional

from .models import ExecutionIntent, ExecutionPlan, ExecutionQualityAssessment, ExecutionResult


def explain_no_execution_intent(strategy_name: str, decision_state: str) -> str:
    return (
        f"{strategy_name}: execution simulation skipped -- decision_state={decision_state!r} "
        f"was never worth exploring further."
    )


def explain_execution_intent(intent: ExecutionIntent, plan: ExecutionPlan) -> str:
    lines = [
        f"Strategy: {intent.strategy_name} (decision_id={intent.decision_id})",
        intent.expected_behavior,
        f"Execution style: {plan.execution_style}. {plan.entry_assumption}",
    ]
    if not plan.simulation_required:
        lines.append(
            "Execution simulation skipped because the Risk Context Adapter's assessment does not "
            "permit exploratory simulation (a real capital restriction, unavailable context, or an "
            "un-evaluated decision)."
        )
    return "\n".join(lines)


def explain_execution_result(result: Optional[ExecutionResult], quality: ExecutionQualityAssessment) -> str:
    if result is None:
        return f"No execution result: {quality.degradation_reason}."
    lines = [
        f"intent_id={result.intent_id}: status={result.status}, fill_quality={result.simulated_fill_quality}",
        result.execution_notes,
    ]
    if quality.quality_score is not None:
        lines.append(f"Quality score: {quality.quality_score:.1f}/100.")
    if quality.degradation_reason is not None:
        lines.append(f"Degradation reason: {quality.degradation_reason}.")
    if quality.learning_tags:
        lines.append("Learning tags: " + ", ".join(quality.learning_tags))
    return "\n".join(lines)
