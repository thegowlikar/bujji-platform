"""Phase 20.19 -- the ONLY concrete `BrokerAdapter` this phase
implements. Reuses Phase 20.18's own `simulate_execution()` directly
-- does not reimplement `FillSimulator`/`SlippageCalculator` or any
part of the simulation math. `adapter_name` is always `"PAPER"` --
never a real broker's name.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from bujji.execution_intelligence import ExecutionPlan, simulate_execution
from bujji.broker.simulation.market_snapshot import MarketSnapshot

from .interface import BrokerAdapter
from .models import (
    STATUS_ADAPTER_FAILED, STATUS_ROUTED_TO_PAPER, STATUS_SIMULATION_UNAVAILABLE,
    BrokerResponse, ExecutionRequest,
)

ADAPTER_NAME = "PAPER"


class PaperBrokerAdapter(BrokerAdapter):
    """Stateless: `get_execution_status()` never has anything to
    return, honestly disclosed rather than fabricating a lookup
    table this phase does not persist."""

    name = ADAPTER_NAME

    def submit_execution_request(
        self, request: ExecutionRequest, plan: ExecutionPlan, snapshot: MarketSnapshot, *, seed: int = 0,
    ) -> BrokerResponse:
        now = request.timestamp  # never a real live clock call in this phase.
        try:
            if not plan.simulation_required:
                return BrokerResponse(
                    adapter_name=ADAPTER_NAME, status=STATUS_SIMULATION_UNAVAILABLE,
                    execution_reference=None, fill_information=None, timestamp=now,
                )

            result = simulate_execution(plan, snapshot, seed=seed)
            if result is None:
                return BrokerResponse(
                    adapter_name=ADAPTER_NAME, status=STATUS_SIMULATION_UNAVAILABLE,
                    execution_reference=None, fill_information=None, timestamp=now,
                )

            return BrokerResponse(
                adapter_name=ADAPTER_NAME, status=STATUS_ROUTED_TO_PAPER,
                execution_reference=f"PAPER-{request.execution_intent_id}",  # paper-only, never an exchange order id.
                fill_information=result, timestamp=now,
            )
        except Exception:
            # Fail closed, honestly -- NEVER falls back to a live adapter.
            return BrokerResponse(
                adapter_name=ADAPTER_NAME, status=STATUS_ADAPTER_FAILED,
                execution_reference=None, fill_information=None, timestamp=request.timestamp,
            )

    def get_execution_status(self, execution_reference: str) -> Optional[BrokerResponse]:
        return None

    def reconcile_execution(
        self, request: ExecutionRequest, response: Optional[BrokerResponse],
    ) -> "ReconciliationResult":
        from .reconciliation import reconcile

        return reconcile(request, response)
