"""Execution Planning Engine models — Series 98. Frozen dataclasses
throughout (house convention)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from bujji.msi_trade_construction.models import StrikeLeg


@dataclass(frozen=True)
class ExecutionStep:
    stage_index: int
    role_filter: str
    legs: Tuple[StrikeLeg, ...]
    verification_required: bool
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class DependencyEdge:
    from_stage: int
    to_stage: int
    reason: str


@dataclass(frozen=True)
class ValidationGate:
    name: str                       # taxonomy.ALL_VALIDATION_GATES
    real_time_evaluable: bool       # False -- disclosed as runtime-only, no real data exists at this layer.
    passed: Optional[bool]          # None iff not evaluable here.
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class FailurePolicy:
    failure_type: str               # taxonomy.ALL_FAILURE_TYPES
    response: str
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class RollbackPolicy:
    policy: str
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class RecoveryPolicy:
    policy: str
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class TimeoutPolicy:
    policy: str
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why_this_sequence: Tuple[str, ...]
    why_this_dependency: Tuple[str, ...]
    why_this_recovery_plan: Tuple[str, ...]
    why_this_validation_order: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class ExecutionPlanAssessment:
    plan_id: str
    timestamp: str
    position_assessment_id: str          # trade.assessment_id (Series 90).
    strategy_family: str
    execution_mode: str                  # taxonomy.EXECUTION_MODE_*
    order_sequence: Tuple[StrikeLeg, ...]  # flat, final placement order.
    execution_steps: Tuple[ExecutionStep, ...]
    dependency_graph: Tuple[DependencyEdge, ...]
    validation_steps: Tuple[ValidationGate, ...]
    failure_policies: Tuple[FailurePolicy, ...]
    rollback_policy: RollbackPolicy
    recovery_policy: RecoveryPolicy
    timeout_policy: TimeoutPolicy
    estimated_orders: int
    estimated_latency: str               # taxonomy.ALL_LATENCY_LEVELS
    explanation: Explanation
    provenance: str
    schema_version: str
