"""Execution Engine runner — composes engine.orchestrate() with
optional journaling. Top-level entry point the future Broker Adapter
calls; never re-implements orchestration logic itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..execution_planner.models import ExecutionPlan
from .engine import Clock, _real_clock, orchestrate
from .models import ExecutionInstructionSet


def run_orchestration(
    execution_plan: Optional[ExecutionPlan],
    clock: Clock = _real_clock,
    journal=None,
) -> ExecutionInstructionSet:
    """Run one orchestration cycle and optionally journal the result.

    `journal`, if supplied, must expose a `.record(instruction_set)`
    method (see bujji/journal/execution_engine_journal.py). This
    runner never connects to a broker, SDK, or order manager -- its
    only input is the ExecutionPlan it is handed.
    """
    instruction_set = orchestrate(execution_plan, clock=clock)

    if journal is not None:
        journal.record(instruction_set)

    return instruction_set
