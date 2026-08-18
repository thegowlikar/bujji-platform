"""Phase 20.20 -- human-readable rendering. No new logic; reads fields
already computed by `builder.py`/`health.py`.
"""
from __future__ import annotations

from .health import PipelineHealthReport
from .models import ShadowResultRecord


def explain_shadow_result(record: ShadowResultRecord) -> str:
    lines = [
        f"Strategy: {record.strategy_name} | record_id={record.record_id} | timestamp={record.timestamp}",
        f"Decision state: {record.decision_state}. Pipeline stage reached: {record.pipeline_stage_reached}.",
    ]
    if record.risk_context_status is not None:
        lines.append(f"Risk context status: {record.risk_context_status}")
    if record.execution_intent_created:
        lines.append(f"Execution intent created. Simulation required: {record.execution_simulation_required}.")
    if record.broker_response_status is not None:
        lines.append(f"Broker response ({record.broker_adapter_name}): {record.broker_response_status}.")
    if record.execution_result_status is not None:
        lines.append(f"Simulated fill status: {record.execution_result_status}.")
    if record.reconciliation_consistency is not None:
        lines.append(f"Reconciliation: {record.reconciliation_consistency}.")
    return "\n".join(lines)


def explain_pipeline_health(report: PipelineHealthReport) -> str:
    lines = [f"Runtime status: {report.runtime_status} ({report.cycles_total} cycles recorded)."]
    for stage_health in report.stage_reach:
        lines.append(f"  {stage_health.stage}: {stage_health.cycles_reached}/{stage_health.cycles_total}")
    rec = report.reconciliation
    lines.append(
        f"Reconciliation: {rec.consistent_count} consistent, {rec.inconsistent_count} inconsistent, "
        f"{rec.missing_response_count} missing (ratio inconsistent={rec.inconsistency_ratio:.2%})."
    )
    lines.append(f"Broker adapter failure ratio: {report.adapter_failure_ratio:.2%}.")
    if report.reasons:
        lines.append("Reasons: " + "; ".join(report.reasons))
    return "\n".join(lines)
