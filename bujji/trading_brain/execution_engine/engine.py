"""Execution Engine engine — BUJJI Options OS v3, Engineering Series
39, Sprint 1.

Everything above this layer has already decided what strategy, whether
it is allowed, how much capital is authorized, and what execution plan
should exist. This module never revisits any of those decisions -- it
only converts an already-produced, broker-independent `ExecutionPlan`
into an ordered sequence of abstract, broker-agnostic workflow
actions. It orchestrates; it does not decide, and it does not execute.

Input is exactly one `ExecutionPlan` (or `None`) -- never MIC v2, the
Market State Builder, the Strategy Selector, the Risk Brain, the
Capital Brain, a broker SDK, a replay engine, or a live feed.

Evaluation is a finite, deterministic decision table. No REST payload,
no order field, no quantity, no strike, no expiry, no retry logic, and
no randomness appears anywhere in this function.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, Optional, Tuple

from ..execution_planner.models import ExecutionPlan
from . import taxonomy
from .models import ExecutionInstructionSet

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def _downgrade_one_step(level: str) -> str:
    idx = taxonomy.CONFIDENCE_ORDER.index(level)
    if level == "UNKNOWN":
        return "UNKNOWN"
    return taxonomy.CONFIDENCE_ORDER[max(idx - 1, 1)]


def _trace(plan_status: str, actions: Tuple[str, ...], status: str) -> str:
    return (
        f"Execution Plan = {plan_status}. "
        f"Actions = {', '.join(actions) if actions else 'none'}. "
        f"Status = {status}."
    )


def _instruction_set_id(plan: Optional[ExecutionPlan], status: str, timestamp: str) -> str:
    seed = "|".join([plan.plan_id if plan else "NONE", status, timestamp])
    return "EIS-" + hashlib.md5(seed.encode()).hexdigest()[:16]


def orchestrate(
    execution_plan: Optional[ExecutionPlan],
    clock: Clock = _real_clock,
) -> ExecutionInstructionSet:
    """Convert an ExecutionPlan into an ordered abstract instruction set.

    Never connects to a broker, never builds a REST payload, never
    calls an SDK, never calculates a quantity, strike, or expiry, and
    never retries anything -- only names a finite status and an
    ordered sequence of workflow actions for a future Broker Adapter
    to implement.
    """
    timestamp = clock().isoformat()

    # Rule 1: nothing to orchestrate from at all.
    if execution_plan is None:
        status = taxonomy.STATUS_UNKNOWN
        actions: Tuple[str, ...] = ()
        controls: Tuple[str, ...] = ("NONE",)
        blocking = (taxonomy.BLOCKING_CONDITION_MISSING_PLAN,)
        confidence = "UNKNOWN"
        return ExecutionInstructionSet(
            instruction_set_id=_instruction_set_id(None, status, timestamp),
            status=status,
            execution_intent="UNKNOWN",
            abstract_actions=actions,
            required_controls=controls,
            blocking_conditions=blocking,
            execution_trace=_trace("NONE", actions, status),
            confidence=confidence,
            plan_id=None,
            timestamp=timestamp,
            version=taxonomy.EXECUTION_ENGINE_VERSION,
        )

    required_controls = execution_plan.required_controls
    execution_intent = execution_plan.execution_intent

    # Rule: the plan itself could not reach a determination.
    if execution_plan.status == "UNKNOWN":
        status = taxonomy.STATUS_UNKNOWN
        actions = ()
        blocking = (taxonomy.BLOCKING_CONDITION_MISSING_PLAN,)
        confidence = execution_plan.confidence
        return ExecutionInstructionSet(
            instruction_set_id=_instruction_set_id(execution_plan, status, timestamp),
            status=status,
            execution_intent=execution_intent,
            abstract_actions=actions,
            required_controls=required_controls,
            blocking_conditions=blocking,
            execution_trace=_trace(execution_plan.status, actions, status),
            confidence=confidence,
            plan_id=execution_plan.plan_id,
            timestamp=timestamp,
            version=taxonomy.EXECUTION_ENGINE_VERSION,
        )

    # Rule 2: no capital was authorized -- nothing actionable.
    if execution_plan.status == "NOT_PLANNED":
        status = taxonomy.STATUS_BLOCKED
        actions = (taxonomy.ACTION_BLOCK,)
        blocking = (taxonomy.BLOCKING_CONDITION_UNKNOWN_INTENT,)
        confidence = execution_plan.confidence
        return ExecutionInstructionSet(
            instruction_set_id=_instruction_set_id(execution_plan, status, timestamp),
            status=status,
            execution_intent=execution_intent,
            abstract_actions=actions,
            required_controls=required_controls,
            blocking_conditions=blocking,
            execution_trace=_trace(execution_plan.status, actions, status),
            confidence=confidence,
            plan_id=execution_plan.plan_id,
            timestamp=timestamp,
            version=taxonomy.EXECUTION_ENGINE_VERSION,
        )

    # Rule 3: upstream planning itself could not resolve a strategy.
    if execution_plan.status == "BLOCKED":
        status = taxonomy.STATUS_BLOCKED
        actions = (taxonomy.ACTION_BLOCK,)
        blocking = (taxonomy.BLOCKING_CONDITION_FAILED_VALIDATION,)
        confidence = execution_plan.confidence
        return ExecutionInstructionSet(
            instruction_set_id=_instruction_set_id(execution_plan, status, timestamp),
            status=status,
            execution_intent=execution_intent,
            abstract_actions=actions,
            required_controls=required_controls,
            blocking_conditions=blocking,
            execution_trace=_trace(execution_plan.status, actions, status),
            confidence=confidence,
            plan_id=execution_plan.plan_id,
            timestamp=timestamp,
            version=taxonomy.EXECUTION_ENGINE_VERSION,
        )

    # From here: execution_plan.status == "PLANNED". Defensive
    # consistency check -- this engine never trusts an upstream object
    # blindly. A PLANNED plan naming no strategy would be a contract
    # violation Execution Planner's own policy never actually
    # produces.
    if execution_plan.status == "PLANNED" and not execution_plan.strategy_id:
        status = taxonomy.STATUS_BLOCKED
        actions = (taxonomy.ACTION_VALIDATE_PLAN, taxonomy.ACTION_BLOCK)
        blocking = (taxonomy.BLOCKING_CONDITION_FAILED_VALIDATION,)
        confidence = _downgrade_one_step(execution_plan.confidence)
        return ExecutionInstructionSet(
            instruction_set_id=_instruction_set_id(execution_plan, status, timestamp),
            status=status,
            execution_intent=execution_intent,
            abstract_actions=actions,
            required_controls=required_controls,
            blocking_conditions=blocking,
            execution_trace=_trace(execution_plan.status, actions, status),
            confidence=confidence,
            plan_id=execution_plan.plan_id,
            timestamp=timestamp,
            version=taxonomy.EXECUTION_ENGINE_VERSION,
        )

    # Rule 4: a clean, well-formed plan -- orchestrate the standard
    # four-step hand-off workflow.
    status = taxonomy.STATUS_READY_FOR_ADAPTER
    actions = (
        taxonomy.ACTION_VALIDATE_PLAN,
        taxonomy.ACTION_VALIDATE_CONTROLS,
        taxonomy.ACTION_AUTHORIZE_EXECUTION,
        taxonomy.ACTION_WAIT_FOR_ADAPTER,
    )
    blocking = ()
    has_real_constraint = execution_plan.execution_constraints != ("NONE",)
    confidence = (
        _downgrade_one_step(execution_plan.confidence)
        if has_real_constraint
        else execution_plan.confidence
    )
    return ExecutionInstructionSet(
        instruction_set_id=_instruction_set_id(execution_plan, status, timestamp),
        status=status,
        execution_intent=execution_intent,
        abstract_actions=actions,
        required_controls=required_controls,
        blocking_conditions=blocking,
        execution_trace=_trace(execution_plan.status, actions, status),
        confidence=confidence,
        plan_id=execution_plan.plan_id,
        timestamp=timestamp,
        version=taxonomy.EXECUTION_ENGINE_VERSION,
    )
