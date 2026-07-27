"""Order Construction Service runner — composes engine.construct_orders()
with optional journaling. Top-level entry point a future component
(e.g. the Runtime Execution Service) calls; never re-implements
construction logic itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..position_sizing.models import PositionPlan
from .engine import Clock, _real_clock, construct_orders
from .models import ExecutionPolicy, OrderConstructionResult, TradingConfiguration


def run_order_construction(
    position_plan: Optional[PositionPlan],
    execution_policy: Optional[ExecutionPolicy],
    trading_config: Optional[TradingConfiguration],
    clock: Clock = _real_clock,
    journal=None,
) -> OrderConstructionResult:
    """Run one order-construction cycle and optionally journal the
    result.

    `journal`, if supplied, must expose a `.record(result)` method
    (see bujji/journal/order_construction_journal.py). This runner
    never connects to a broker, never authenticates -- its only inputs
    are the objects it is handed.
    """
    result = construct_orders(position_plan, execution_policy, trading_config, clock=clock)

    if journal is not None:
        journal.record(result)

    return result
