"""Execution Planner engine — BUJJI Options OS v3, Engineering Series
37, Sprint 1.

The Capital Brain answered "how much capital may be committed?" This
module answers a deliberately narrower question: "what
broker-independent execution PLAN should be created from that
authorization?" It produces an ordered list of abstract, conceptual
workflow steps -- never an order, never a broker payload, never a
strike, expiry, or quantity.

Inputs are exactly two already-produced objects -- `CapitalDecision`
and `StrategyDecision` -- read only, never recomputed, never mutated.
No broker SDK, execution engine, order manager, MIC v2, live feed,
replay engine, or market data import exists anywhere in this package.

Evaluation is a finite, deterministic decision table. No randomness,
no machine learning, no broker-specific logic of any kind appears
anywhere in this function.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from ..capital_brain.models import CapitalDecision
from ..strategy_selector.models import StrategyDecision
from . import taxonomy
from .models import ExecutionPlan, ExecutionStep

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def _downgrade_one_step(level: str) -> str:
    idx = taxonomy.CONFIDENCE_ORDER.index(level)
    if level == "UNKNOWN":
        return "UNKNOWN"
    return taxonomy.CONFIDENCE_ORDER[max(idx - 1, 1)]


def _standard_steps(strategy_id: str) -> Tuple[ExecutionStep, ...]:
    """The conceptual workflow every PLANNED plan carries.

    Purely descriptive workflow phases -- never a broker instruction,
    order field, or payload.
    """
    return (
        ExecutionStep(
            taxonomy.STEP_TYPE_VALIDATE,
            "Validate prerequisites: capital authorization and strategy selection are both present.",
        ),
        ExecutionStep(taxonomy.STEP_TYPE_VALIDATE, "Confirm capital authorization."),
        ExecutionStep(taxonomy.STEP_TYPE_VALIDATE, f"Confirm strategy selection ({strategy_id})."),
        ExecutionStep(taxonomy.STEP_TYPE_PREPARE, "Prepare execution."),
        ExecutionStep(taxonomy.STEP_TYPE_WAIT, "Await execution engine."),
    )


def _plan_id(
    capital_decision: Optional[CapitalDecision],
    strategy_decision: Optional[StrategyDecision],
    status: str,
    execution_intent: str,
    timestamp: str,
) -> str:
    seed = "|".join(
        [
            capital_decision.decision_id if capital_decision else "NONE",
            strategy_decision.decision_id if strategy_decision else "NONE",
            status,
            execution_intent,
            timestamp,
        ]
    )
    return "EP-" + hashlib.md5(seed.encode()).hexdigest()[:16]


def _trace(
    capital_decision: Optional[CapitalDecision],
    strategy_id: Optional[str],
    execution_intent: str,
    constraints: Tuple[str, ...],
    status: str,
) -> str:
    return (
        f"Capital Decision = {capital_decision.capital_intent if capital_decision else 'NONE'}. "
        f"Strategy = {strategy_id if strategy_id else 'NONE'}. "
        f"Execution Intent = {execution_intent}. "
        f"Constraint = {', '.join(constraints)}. "
        f"Status = {status}."
    )


def plan(
    capital_decision: Optional[CapitalDecision],
    strategy_decision: Optional[StrategyDecision],
    clock: Clock = _real_clock,
) -> ExecutionPlan:
    """Transform a capital authorization and strategy selection into a
    broker-independent execution plan.

    Never places an order, calculates a quantity, generates a broker
    payload, chooses a strike or expiry, or connects to any broker --
    only names a finite status, execution intent, and ordered list of
    conceptual workflow steps for a future Execution Engine to
    implement however it likes.
    """
    timestamp = clock().isoformat()

    # Rule 1: nothing to plan from at all.
    if capital_decision is None:
        status = taxonomy.STATUS_UNKNOWN
        execution_intent = taxonomy.EXECUTION_INTENT_UNKNOWN
        constraints: Tuple[str, ...] = (taxonomy.CONSTRAINT_NONE,)
        confidence = "UNKNOWN"
        strategy_id = None
        capital_intent = "UNKNOWN"
        required_controls: Tuple[str, ...] = (taxonomy.CONSTRAINT_NONE,)
        steps: Tuple[ExecutionStep, ...] = ()
        return ExecutionPlan(
            plan_id=_plan_id(None, strategy_decision, status, execution_intent, timestamp),
            status=status,
            execution_intent=execution_intent,
            strategy_id=strategy_id,
            capital_intent=capital_intent,
            required_controls=required_controls,
            execution_constraints=constraints,
            execution_steps=steps,
            confidence=confidence,
            planning_trace=_trace(None, strategy_id, execution_intent, constraints, status),
            capital_decision_id=None,
            strategy_decision_id=strategy_decision.decision_id if strategy_decision else None,
            timestamp=timestamp,
            version=taxonomy.EXECUTION_PLANNER_VERSION,
        )

    required_controls = capital_decision.required_controls
    capital_intent = capital_decision.capital_intent

    # Rule 2: allocation was denied -- nothing to plan.
    if capital_decision.allocation_status == "DENIED":
        status = taxonomy.STATUS_NOT_PLANNED
        execution_intent = taxonomy.EXECUTION_INTENT_NONE
        constraints = (taxonomy.CONSTRAINT_NONE,)
        confidence = capital_decision.confidence
        strategy_id = strategy_decision.selected_strategy if strategy_decision else None
        steps = ()
        return ExecutionPlan(
            plan_id=_plan_id(capital_decision, strategy_decision, status, execution_intent, timestamp),
            status=status,
            execution_intent=execution_intent,
            strategy_id=strategy_id,
            capital_intent=capital_intent,
            required_controls=required_controls,
            execution_constraints=constraints,
            execution_steps=steps,
            confidence=confidence,
            planning_trace=_trace(capital_decision, strategy_id, execution_intent, constraints, status),
            capital_decision_id=capital_decision.decision_id,
            strategy_decision_id=strategy_decision.decision_id if strategy_decision else None,
            timestamp=timestamp,
            version=taxonomy.EXECUTION_PLANNER_VERSION,
        )

    # Capital Brain itself could not reach a determination.
    if capital_decision.allocation_status == "UNKNOWN":
        status = taxonomy.STATUS_UNKNOWN
        execution_intent = taxonomy.EXECUTION_INTENT_UNKNOWN
        constraints = (taxonomy.CONSTRAINT_NONE,)
        confidence = capital_decision.confidence
        steps = ()
        return ExecutionPlan(
            plan_id=_plan_id(capital_decision, strategy_decision, status, execution_intent, timestamp),
            status=status,
            execution_intent=execution_intent,
            strategy_id=None,
            capital_intent=capital_intent,
            required_controls=required_controls,
            execution_constraints=constraints,
            execution_steps=steps,
            confidence=confidence,
            planning_trace=_trace(capital_decision, None, execution_intent, constraints, status),
            capital_decision_id=capital_decision.decision_id,
            strategy_decision_id=strategy_decision.decision_id if strategy_decision else None,
            timestamp=timestamp,
            version=taxonomy.EXECUTION_PLANNER_VERSION,
        )

    # From here, allocation_status is LIMITED or APPROVED -- capital
    # would otherwise support planning. Only now does a missing or
    # unresolved strategy matter.
    if strategy_decision is None or strategy_decision.selected_strategy is None:
        status = taxonomy.STATUS_BLOCKED
        execution_intent = taxonomy.EXECUTION_INTENT_UNKNOWN
        constraints = (taxonomy.CONSTRAINT_MANUAL_APPROVAL,)
        confidence = _downgrade_one_step(capital_decision.confidence)
        steps = ()
        return ExecutionPlan(
            plan_id=_plan_id(capital_decision, strategy_decision, status, execution_intent, timestamp),
            status=status,
            execution_intent=execution_intent,
            strategy_id=None,
            capital_intent=capital_intent,
            required_controls=required_controls,
            execution_constraints=constraints,
            execution_steps=steps,
            confidence=confidence,
            planning_trace=_trace(capital_decision, None, execution_intent, constraints, status),
            capital_decision_id=capital_decision.decision_id,
            strategy_decision_id=strategy_decision.decision_id if strategy_decision else None,
            timestamp=timestamp,
            version=taxonomy.EXECUTION_PLANNER_VERSION,
        )

    strategy_id = strategy_decision.selected_strategy

    # Rule 3: limited allocation -- plan, but under named constraints.
    if capital_decision.allocation_status == "LIMITED":
        status = taxonomy.STATUS_PLANNED
        execution_intent = taxonomy.EXECUTION_INTENT_PREPARE
        constraints = (taxonomy.CONSTRAINT_FOLLOW_RISK_CONTROLS, taxonomy.CONSTRAINT_REDUCE_SIZE)
        confidence = _downgrade_one_step(capital_decision.confidence)
        steps = _standard_steps(strategy_id)
        return ExecutionPlan(
            plan_id=_plan_id(capital_decision, strategy_decision, status, execution_intent, timestamp),
            status=status,
            execution_intent=execution_intent,
            strategy_id=strategy_id,
            capital_intent=capital_intent,
            required_controls=required_controls,
            execution_constraints=constraints,
            execution_steps=steps,
            confidence=confidence,
            planning_trace=_trace(capital_decision, strategy_id, execution_intent, constraints, status),
            capital_decision_id=capital_decision.decision_id,
            strategy_decision_id=strategy_decision.decision_id,
            timestamp=timestamp,
            version=taxonomy.EXECUTION_PLANNER_VERSION,
        )

    # Rule 4: approved allocation -- plan cleanly.
    if capital_decision.allocation_status == "APPROVED":
        status = taxonomy.STATUS_PLANNED
        execution_intent = taxonomy.EXECUTION_INTENT_PREPARE
        constraints = (taxonomy.CONSTRAINT_NONE,)
        confidence = capital_decision.confidence
        steps = _standard_steps(strategy_id)
        return ExecutionPlan(
            plan_id=_plan_id(capital_decision, strategy_decision, status, execution_intent, timestamp),
            status=status,
            execution_intent=execution_intent,
            strategy_id=strategy_id,
            capital_intent=capital_intent,
            required_controls=required_controls,
            execution_constraints=constraints,
            execution_steps=steps,
            confidence=confidence,
            planning_trace=_trace(capital_decision, strategy_id, execution_intent, constraints, status),
            capital_decision_id=capital_decision.decision_id,
            strategy_decision_id=strategy_decision.decision_id,
            timestamp=timestamp,
            version=taxonomy.EXECUTION_PLANNER_VERSION,
        )

    # Defensive: an unrecognized allocation_status.
    status = taxonomy.STATUS_UNKNOWN
    execution_intent = taxonomy.EXECUTION_INTENT_UNKNOWN
    constraints = (taxonomy.CONSTRAINT_NONE,)
    confidence = "UNKNOWN"
    steps = ()
    return ExecutionPlan(
        plan_id=_plan_id(capital_decision, strategy_decision, status, execution_intent, timestamp),
        status=status,
        execution_intent=execution_intent,
        strategy_id=strategy_id,
        capital_intent=capital_intent,
        required_controls=required_controls,
        execution_constraints=constraints,
        execution_steps=steps,
        confidence=confidence,
        planning_trace=_trace(capital_decision, strategy_id, execution_intent, constraints, status),
        capital_decision_id=capital_decision.decision_id,
        strategy_decision_id=strategy_decision.decision_id,
        timestamp=timestamp,
        version=taxonomy.EXECUTION_PLANNER_VERSION,
    )
