"""Execution Planner runner — composes engine.plan() with optional
journaling. Top-level entry point future Trading Brain modules call;
never re-implements planning logic itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..capital_brain.models import CapitalDecision
from ..strategy_selector.models import StrategyDecision
from .engine import Clock, _real_clock, plan
from .models import ExecutionPlan


def run_planning(
    capital_decision: Optional[CapitalDecision],
    strategy_decision: Optional[StrategyDecision],
    clock: Clock = _real_clock,
    journal=None,
) -> ExecutionPlan:
    """Run one planning cycle and optionally journal the result.

    `journal`, if supplied, must expose a `.record(plan)` method (see
    bujji/journal/execution_planner_journal.py). This runner never
    reads MIC v2, the Market State Builder, the Risk Brain, or any
    earlier upstream layer directly -- its only inputs are the
    CapitalDecision and StrategyDecision it is handed.
    """
    execution_plan = plan(capital_decision, strategy_decision, clock=clock)

    if journal is not None:
        journal.record(execution_plan)

    return execution_plan
