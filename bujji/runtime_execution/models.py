"""Runtime Execution Orchestrator models — frozen, immutable records.

Nothing here authenticates, retries, polls, or reconciles. This module
owns workflow only; every field here describes a validated, prepared
session and its dispatch plan -- never a broker-side outcome (a fill,
a rejection, a broker order id). Those remain entirely inside
production's own `Broker`/`ExecutionEngine`/`OrderResult`, never
redefined or shadowed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from ..trading_brain.order_construction.models import OrderRequest


@dataclass(frozen=True)
class DispatchInstruction:
    sequence: int
    client_order_id: str
    order_request: OrderRequest


@dataclass(frozen=True)
class ExecutionSession:
    session_id: str
    order_requests: Tuple[OrderRequest, ...]
    execution_state: str
    dispatch_plan: Tuple[DispatchInstruction, ...]
    validation_result: str
    execution_trace: str
    failure_reason: Optional[str]
    timestamp: str
    version: str
