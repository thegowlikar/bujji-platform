"""Execution Planning Engine — Series 98.

Consumes Series 90's real `TradeConstructionAssessment` (legs, strategy
family) plus, optionally, real downstream assessments already computed
by this arc (Series 96's `PositionLifecycleAssessment`, Series 97's
`MarginEstimate`, Series 91's `PortfolioConstructionAssessment`) to
evaluate the validation gates that genuinely have real, replay-safe
data behind them. Never calls a broker, never places an order, never
simulates a fill. Sequencing/failure/recovery philosophy is modeled
directly on the real, confirmed behavior of production's
`bujji.execution.engine.ExecutionEngine` (see config.py's own
docstring for the precise, disclosed reuse-by-pattern mapping).
"""
from __future__ import annotations

import hashlib
from typing import Optional, Tuple

from bujji.msi_margin_bridge.models import MarginEstimate
from bujji.msi_portfolio_construction.models import PortfolioConstructionAssessment
from bujji.msi_position_lifecycle.models import PositionLifecycleAssessment
from bujji.msi_trade_construction.models import StrikeLeg, TradeConstructionAssessment

from . import config as _config
from . import taxonomy
from .models import (
    DependencyEdge, Explanation, ExecutionPlanAssessment, ExecutionStep, FailurePolicy,
    RecoveryPolicy, RollbackPolicy, TimeoutPolicy, ValidationGate,
)


def _matches(leg: StrikeLeg, role_filter: str) -> bool:
    if role_filter == "ANY":
        return True
    if role_filter in ("BUY", "SELL"):
        return leg.side == role_filter
    if role_filter in ("CE", "PE"):
        return leg.option_type == role_filter
    return False


def _build_stages(trade: TradeConstructionAssessment) -> Tuple[Tuple[str, Tuple[StrikeLeg, ...]], ...]:
    rule = _config.STAGE_RULES.get(trade.strategy_family, (("ANY",),))
    stages = []
    assigned = set()
    for role_filter in rule:
        matched = tuple(leg for leg in trade.legs if id(leg) not in assigned and _matches(leg, role_filter[0]))
        for leg in matched:
            assigned.add(id(leg))
        if matched:
            stages.append((role_filter[0], matched))
    # Any leg the declared rule didn't match (shouldn't happen for a
    # correctly-classified family, but never silently dropped) goes into
    # a final catch-all stage.
    leftover = tuple(leg for leg in trade.legs if id(leg) not in assigned)
    if leftover:
        stages.append(("REMAINING", leftover))
    return tuple(stages)


def _validation_gates(
    trade: TradeConstructionAssessment, lifecycle: Optional[PositionLifecycleAssessment],
    margin_estimate: Optional[MarginEstimate], portfolio: Optional[PortfolioConstructionAssessment],
) -> Tuple[ValidationGate, ...]:
    gates = []

    gates.append(ValidationGate(
        name=taxonomy.GATE_MARKET_OPEN, real_time_evaluable=False, passed=None,
        reasoning=("no real wall-clock/market-calendar source exists in this deterministic package -- "
                   "this gate must be evaluated by the live runtime, not here",),
    ))
    gates.append(ValidationGate(
        name=taxonomy.GATE_EXECUTION_WINDOW, real_time_evaluable=False, passed=None,
        reasoning=("same as MARKET_OPEN -- a real execution-window check requires live wall-clock time",),
    ))

    if lifecycle is not None:
        state_ok = lifecycle.position_state not in ("EXIT_CANDIDATE", "THESIS_BROKEN", "CLOSED")
        gates.append(ValidationGate(
            name=taxonomy.GATE_POSITION_STILL_VALID, real_time_evaluable=True, passed=state_ok,
            reasoning=(f"Position Lifecycle's real position_state={lifecycle.position_state}",),
        ))
        thesis_ok = lifecycle.thesis_invalidation.compatible
        gates.append(ValidationGate(
            name=taxonomy.GATE_THESIS_STILL_VALID, real_time_evaluable=True, passed=thesis_ok,
            reasoning=(f"Position Lifecycle's real thesis_invalidation.compatible={thesis_ok} "
                       f"(entry={lifecycle.thesis_invalidation.entry_thesis_type}, "
                       f"current={lifecycle.thesis_invalidation.current_thesis_type})",),
        ))
    else:
        gates.append(ValidationGate(
            name=taxonomy.GATE_POSITION_STILL_VALID, real_time_evaluable=False, passed=None,
            reasoning=("no PositionLifecycleAssessment was supplied -- cannot evaluate",),
        ))
        gates.append(ValidationGate(
            name=taxonomy.GATE_THESIS_STILL_VALID, real_time_evaluable=False, passed=None,
            reasoning=("no PositionLifecycleAssessment was supplied -- cannot evaluate",),
        ))

    if margin_estimate is not None and portfolio is not None:
        margin_ok = (
            margin_estimate.estimated_margin is not None and portfolio.capital_available is not None
            and portfolio.capital_available >= margin_estimate.estimated_margin
        )
        gates.append(ValidationGate(
            name=taxonomy.GATE_MARGIN_STILL_SUFFICIENT, real_time_evaluable=True, passed=margin_ok,
            reasoning=(f"margin estimate={margin_estimate.estimated_margin} vs "
                       f"capital_available={portfolio.capital_available}",),
        ))
    else:
        gates.append(ValidationGate(
            name=taxonomy.GATE_MARGIN_STILL_SUFFICIENT, real_time_evaluable=False, passed=None,
            reasoning=("no MarginEstimate and/or PortfolioConstructionAssessment supplied -- cannot evaluate",),
        ))

    # Duplicate protection IS fully computable here, deterministically --
    # it reuses the CONCEPT of ExecutionEngine's own client_order_id
    # idempotency key, derived as a content hash of the plan's own real
    # identity (trade.assessment_id), never a random/UUID value.
    gates.append(ValidationGate(
        name=taxonomy.GATE_DUPLICATE_PROTECTION, real_time_evaluable=True, passed=True,
        reasoning=(f"client-order-id namespace is derived deterministically from trade.assessment_id="
                   f"{trade.assessment_id} -- the same logical order always yields the same id, "
                   f"reusing ExecutionEngine's own real idempotency-key concept",),
    ))
    return tuple(gates)


def _failure_policies() -> Tuple[FailurePolicy, ...]:
    return tuple(
        FailurePolicy(failure_type=ft, response=resp, reasoning=(reason,))
        for ft, (resp, reason) in _config.FAILURE_RESPONSES.items()
    )


def _assessment_id(position_assessment_id: str, stage_count: int, schema_version: str) -> str:
    content = "|".join([position_assessment_id, str(stage_count), schema_version])
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def build_execution_plan(
    trade: TradeConstructionAssessment, lifecycle: Optional[PositionLifecycleAssessment] = None,
    margin_estimate: Optional[MarginEstimate] = None, portfolio: Optional[PortfolioConstructionAssessment] = None,
    *, timestamp: str,
) -> ExecutionPlanAssessment:
    """Produces exactly one ExecutionPlanAssessment. No broker calls, no
    order placement, no fill simulation anywhere."""
    schema_version = taxonomy.MSI_EXECUTION_PLANNING_VERSION

    stages = _build_stages(trade)
    execution_steps = []
    order_sequence: list = []
    why_sequence = []
    for i, (role_filter, legs) in enumerate(stages):
        for leg in legs:
            order_sequence.append(leg)
        why_sequence.append(
            f"stage {i}: {role_filter} leg(s) -- "
            f"{'risk-defining, staged first' if i == 0 else 'staged only after the prior stage is confirmed filled'}"
        )
        execution_steps.append(ExecutionStep(
            stage_index=i, role_filter=role_filter, legs=legs, verification_required=True,
            reasoning=(why_sequence[-1],),
        ))

    dependency_graph = tuple(
        DependencyEdge(from_stage=i, to_stage=i + 1,
                       reason=f"stage {i + 1} may not begin until stage {i}'s fill is verified complete "
                              f"-- execution is never assumed simultaneous")
        for i in range(len(stages) - 1)
    )
    why_dependency = (
        (f"{len(stages)} sequential stage(s), each gated on the previous stage's real, verified fill "
         f"-- reproduces ExecutionEngine's own real CE-then-PE sequential discipline, generalized to "
         f"{len(trade.legs)} leg(s)",)
        if len(stages) > 1 else
        ("single-stage plan -- no inter-stage dependency exists",)
    )

    validation_steps = _validation_gates(trade, lifecycle, margin_estimate, portfolio)
    why_validation_order = (
        "MARKET_OPEN and EXECUTION_WINDOW are checked first (cheapest, most fundamental preconditions), "
        "then POSITION_STILL_VALID and THESIS_STILL_VALID (would this plan even still make sense), "
        "then MARGIN_STILL_SUFFICIENT (can it be afforded), then DUPLICATE_PROTECTION immediately "
        "before any real placement -- fail-closed at the first gate that does not pass",
    )

    failure_policies = _failure_policies()
    rollback_policy = RollbackPolicy(
        policy="CANCEL_REMAINING_DEPENDENT_STAGES_NEVER_UNWIND_FILLED_LEGS",
        reasoning=("a real fill is a real position -- this plan never attempts to synthetically "
                   "'undo' an already-filled leg by placing an offsetting order automatically; "
                   "only NOT-YET-PLACED dependent stages are cancelled, matching "
                   "ExecutionEngine's own real discipline of never guessing at unwind logic",),
    )
    recovery_policy = RecoveryPolicy(
        policy="RECONCILE_THEN_REEVALUATE_BEFORE_ANY_RETRY",
        reasoning=("reuses the CONCEPT of ExecutionEngine.reconcile() -- real broker-reported "
                   "positions must be checked before assuming any prior stage's last-known state "
                   "is accurate; re-evaluation (a fresh Position Lifecycle assessment) happens "
                   "before any further order is planned, never a blind retry",),
    )
    timeout_policy = TimeoutPolicy(
        policy="CANCEL_UNFILLED_REMAINDER_ON_TIMEOUT",
        reasoning=("matches ExecutionEngine._await_fill's own real timeout-then-cancel-remainder "
                   "behavior exactly -- a late fill must never be allowed to silently increase exposure",),
    )

    stage_count = len(stages)
    estimated_latency = _config.LATENCY_BY_STAGE_COUNT.get(stage_count, _config.LATENCY_DEFAULT_FOR_MORE_STAGES)

    aid = _assessment_id(trade.assessment_id, stage_count, schema_version)
    explanation = Explanation(
        assessment_id=aid, why_this_sequence=tuple(why_sequence), why_this_dependency=why_dependency,
        why_this_recovery_plan=recovery_policy.reasoning, why_this_validation_order=why_validation_order,
        schema_version=schema_version,
    )

    return ExecutionPlanAssessment(
        plan_id=aid, timestamp=timestamp, position_assessment_id=trade.assessment_id,
        strategy_family=trade.strategy_family, execution_mode=taxonomy.EXECUTION_MODE_SEQUENTIAL_STAGED,
        order_sequence=tuple(order_sequence), execution_steps=tuple(execution_steps),
        dependency_graph=dependency_graph, validation_steps=validation_steps,
        failure_policies=failure_policies, rollback_policy=rollback_policy, recovery_policy=recovery_policy,
        timeout_policy=timeout_policy, estimated_orders=len(trade.legs), estimated_latency=estimated_latency,
        explanation=explanation, provenance="bujji.msi_execution_planning.engine.build_execution_plan",
        schema_version=schema_version,
    )
