"""Runtime Execution Orchestrator runner — composes
engine.build_session() (and optionally queue_for_dispatch()/dispatch())
with optional journaling. Top-level entry point a future integration
point calls; never re-implements orchestration logic itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from ..trading_brain.order_construction.models import OrderRequest
from .engine import Clock, ExecutionEngineInterface, _real_clock, build_session, dispatch, queue_for_dispatch
from .models import ExecutionSession


def run_session_build(
    order_requests: Optional[Tuple[OrderRequest, ...]],
    clock: Clock = _real_clock,
    journal=None,
) -> ExecutionSession:
    """Build one ExecutionSession and optionally journal the result.

    `journal`, if supplied, must expose a `.record(session)` method
    (see bujji/journal/runtime_execution_journal.py). This runner
    never connects to a broker, never authenticates -- its only input
    is the OrderRequest tuple it is handed.
    """
    session = build_session(order_requests, clock=clock)

    if journal is not None:
        journal.record(session)

    return session


def run_session_dispatch(
    session: ExecutionSession,
    executor: Optional[ExecutionEngineInterface] = None,
    clock: Clock = _real_clock,
    journal=None,
) -> ExecutionSession:
    """Queue and, if an executor is supplied, dispatch an already-built
    ExecutionSession, optionally journaling each resulting state.

    Never performs I/O itself -- any I/O happens entirely inside
    `executor`, which this function never constructs.
    """
    queued = queue_for_dispatch(session, clock=clock)
    if journal is not None:
        journal.record(queued)

    if executor is None:
        return queued

    dispatched = dispatch(queued, executor, clock=clock)
    if journal is not None:
        journal.record(dispatched)

    return dispatched
