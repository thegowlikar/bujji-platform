"""Phase 20.18 -- execution quality measurement. Feedback only; never
writes into `evidence_score`/`confidence`/qualification/ranking (that
boundary belongs to Market Memory / `memory_intelligence`, Phase
20.15.1's own precedent -- this package produces a
`ExecutionQualityAssessment` a future phase MAY choose to feed into
Market Memory, but never does so itself).
"""
from __future__ import annotations

from typing import Optional

from .models import (
    QUALITY_DEGRADED, QUALITY_GOOD, QUALITY_REJECTED,
    ExecutionQualityAssessment, ExecutionResult,
)

# Illustrative, disclosed scoring weights -- not calibrated against
# real execution data (none exists yet; this is Cycle 1's first
# execution-simulation layer).
_SLIPPAGE_PENALTY_PER_UNIT = 5.0
_LATENCY_PENALTY_PER_100MS = 2.0


def assess_execution_quality(result: Optional[ExecutionResult]) -> ExecutionQualityAssessment:
    if result is None:
        return ExecutionQualityAssessment(
            quality_score=None, degradation_reason="SIMULATION_NOT_RUN", learning_tags=(),
        )

    if result.simulated_fill_quality == QUALITY_REJECTED:
        return ExecutionQualityAssessment(
            quality_score=0.0, degradation_reason="SIMULATED_REJECTION", learning_tags=("EXECUTION_REJECTED",),
        )

    slippage_penalty = result.slippage_observed * _SLIPPAGE_PENALTY_PER_UNIT
    latency_penalty = (result.latency_observed / 100.0) * _LATENCY_PENALTY_PER_100MS
    quality_score = max(0.0, 100.0 - slippage_penalty - latency_penalty)

    tags = []
    degradation_reason = None
    if result.simulated_fill_quality == QUALITY_DEGRADED:
        if slippage_penalty >= latency_penalty:
            degradation_reason = "SLIPPAGE_INDUCED_DEGRADATION"
            tags.append("HIGH_SLIPPAGE")
        else:
            degradation_reason = "LATENCY_INDUCED_DEGRADATION"
            tags.append("HIGH_LATENCY")
    else:
        tags.append("CLEAN_FILL")

    return ExecutionQualityAssessment(
        quality_score=quality_score, degradation_reason=degradation_reason, learning_tags=tuple(tags),
    )
