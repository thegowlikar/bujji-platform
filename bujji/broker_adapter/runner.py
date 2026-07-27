"""Broker Adapter runner — composes engine.translate() with optional
journaling. Top-level entry point a future Runtime Execution Service
calls; never re-implements translation logic itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..trading_brain.execution_engine.models import ExecutionInstructionSet
from . import taxonomy
from .engine import Clock, _real_clock, translate
from .models import BrokerExecutionRequest


def run_translation(
    instruction_set: Optional[ExecutionInstructionSet],
    broker: str = taxonomy.BROKER_FYERS,
    clock: Clock = _real_clock,
    journal=None,
) -> BrokerExecutionRequest:
    """Run one translation cycle and optionally journal the result.

    `journal`, if supplied, must expose a `.record(request)` method
    (see bujji/journal/broker_adapter_journal.py). This runner never
    connects to a broker, authenticates, or submits an order -- its
    only input is the ExecutionInstructionSet it is handed.
    """
    request = translate(instruction_set, broker=broker, clock=clock)

    if journal is not None:
        journal.record(request)

    return request
