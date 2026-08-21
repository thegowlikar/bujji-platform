"""Phase 20.20 -- pipeline observability. Mirrors `bujji.
live_shadow_runner.health`'s own established discipline (Phase 20.13):
independent dimensions, every field computed directly from real
recorded state, never estimated -- extended here to cover the stages
Phase 20.13 could not (Risk Context, Execution Intelligence, Broker
Boundary) since those packages did not exist yet when it was built.
`bujji.live_shadow_runner.health` itself is not imported or modified
-- its own scope (feed/intelligence/runtime) is unrelated to this
package's own decision-execution-lifecycle scope.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

from bujji.broker_boundary import CONSISTENT, INCONSISTENT, MISSING_RESPONSE, STATUS_ADAPTER_FAILED

from .models import STAGE_BROKER_ROUTED, STAGE_DECISION_ONLY, STAGE_RECONCILED, ShadowResultRecord

RUNTIME_HEALTHY = "HEALTHY"
RUNTIME_DEGRADED = "DEGRADED"
RUNTIME_EMPTY = "EMPTY"

# Disclosed, not tuned -- same "documented threshold, not optimized"
# discipline every prior phase's own constants use.
_DEGRADED_INCONSISTENCY_RATIO = 0.2
_DEGRADED_ADAPTER_FAILURE_RATIO = 0.1


@dataclass(frozen=True)
class PipelineStageHealth:
    stage: str
    cycles_reached: int
    cycles_total: int


@dataclass(frozen=True)
class ReconciliationHealth:
    consistent_count: int
    inconsistent_count: int
    missing_response_count: int
    inconsistency_ratio: float


@dataclass(frozen=True)
class PipelineHealthReport:
    runtime_status: str
    cycles_total: int
    stage_reach: Tuple[PipelineStageHealth, ...]
    reconciliation: ReconciliationHealth
    adapter_failure_ratio: float
    reasons: Tuple[str, ...]


def compute_pipeline_health(records: Sequence[ShadowResultRecord]) -> PipelineHealthReport:
    from .models import ALL_PIPELINE_STAGES

    total = len(records)
    if total == 0:
        return PipelineHealthReport(
            runtime_status=RUNTIME_EMPTY, cycles_total=0, stage_reach=(),
            reconciliation=ReconciliationHealth(0, 0, 0, 0.0), adapter_failure_ratio=0.0,
            reasons=("no ShadowResultRecords recorded yet",),
        )

    stage_counts = {stage: 0 for stage in ALL_PIPELINE_STAGES}
    for r in records:
        stage_counts[r.pipeline_stage_reached] += 1
    stage_reach = tuple(PipelineStageHealth(stage=s, cycles_reached=stage_counts[s], cycles_total=total)
                         for s in ALL_PIPELINE_STAGES)

    consistent = sum(1 for r in records if r.reconciliation_consistency == CONSISTENT)
    inconsistent = sum(1 for r in records if r.reconciliation_consistency == INCONSISTENT)
    missing = sum(1 for r in records if r.reconciliation_consistency == MISSING_RESPONSE)
    reconciled_total = consistent + inconsistent + missing
    inconsistency_ratio = (inconsistent / reconciled_total) if reconciled_total else 0.0
    reconciliation = ReconciliationHealth(
        consistent_count=consistent, inconsistent_count=inconsistent, missing_response_count=missing,
        inconsistency_ratio=inconsistency_ratio,
    )

    adapter_failures = sum(1 for r in records if r.broker_response_status == STATUS_ADAPTER_FAILED)
    routed_total = sum(1 for r in records if r.pipeline_stage_reached in (STAGE_BROKER_ROUTED, STAGE_RECONCILED))
    adapter_failure_ratio = (adapter_failures / routed_total) if routed_total else 0.0

    reasons = []
    if inconsistency_ratio > _DEGRADED_INCONSISTENCY_RATIO:
        reasons.append(f"reconciliation: {inconsistent}/{reconciled_total} cycles INCONSISTENT")
    if adapter_failure_ratio > _DEGRADED_ADAPTER_FAILURE_RATIO:
        reasons.append(f"broker adapter: {adapter_failures}/{routed_total} cycles ADAPTER_FAILED")

    runtime_status = RUNTIME_DEGRADED if reasons else RUNTIME_HEALTHY

    return PipelineHealthReport(
        runtime_status=runtime_status, cycles_total=total, stage_reach=stage_reach,
        reconciliation=reconciliation, adapter_failure_ratio=adapter_failure_ratio, reasons=tuple(reasons),
    )
