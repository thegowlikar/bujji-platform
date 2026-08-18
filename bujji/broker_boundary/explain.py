"""Phase 20.19 -- human-readable rendering. No new logic; reads fields
already computed by `paper_adapter.py`/`reconciliation.py`.
"""
from __future__ import annotations

from typing import Optional

from .models import (
    CONSISTENT, STATUS_ADAPTER_FAILED, STATUS_ROUTED_TO_PAPER, STATUS_SIMULATION_UNAVAILABLE,
    BrokerResponse, ExecutionRequest, ReconciliationResult,
)


def explain_broker_response(request: ExecutionRequest, response: Optional[BrokerResponse]) -> str:
    if response is None:
        return f"{request.strategy_name}: no broker response was produced."
    if response.status == STATUS_ROUTED_TO_PAPER:
        return f"{request.strategy_name}: execution request routed to {response.adapter_name}BrokerAdapter."
    if response.status == STATUS_SIMULATION_UNAVAILABLE:
        return (
            f"{request.strategy_name}: live broker unavailable by design -- "
            f"{response.adapter_name}BrokerAdapter did not run a simulation (risk_context_status="
            f"{request.risk_context_status!r})."
        )
    return f"{request.strategy_name}: {response.adapter_name}BrokerAdapter reported {STATUS_ADAPTER_FAILED}."


def explain_reconciliation(result: ReconciliationResult) -> str:
    prefix = "Reconciliation successful." if result.consistency == CONSISTENT else "Execution mismatch detected."
    return f"{prefix} {result.explanation}"
