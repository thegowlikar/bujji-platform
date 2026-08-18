"""Phase 20.19 -- execution reconciliation. Compares what Bujji
requested against what the adapter actually returned. Pure, no I/O,
no broker calls -- never a real fill-quantity reconciliation (that
remains Gate A's own `position_group_fill_reconciliation.py`, a
different domain this package never touches).
"""
from __future__ import annotations

from typing import Optional

from .models import (
    CONSISTENT, INCONSISTENT, MISSING_RESPONSE,
    STATUS_ADAPTER_FAILED, STATUS_ROUTED_TO_PAPER, STATUS_SIMULATION_UNAVAILABLE,
    BrokerResponse, ExecutionRequest, ReconciliationResult,
)

EXPECTED_ROUTED = "PAPER_EXECUTION_EXPECTED"
EXPECTED_UNAVAILABLE = "NO_EXECUTION_EXPECTED_OR_SIMULATION_INELIGIBLE"


def reconcile(request: ExecutionRequest, response: Optional[BrokerResponse]) -> ReconciliationResult:
    request_id = request.execution_intent_id

    if response is None:
        return ReconciliationResult(
            request_id=request_id, expected_state=EXPECTED_ROUTED, observed_state="NO_RESPONSE",
            consistency=MISSING_RESPONSE,
            explanation=f"{request_id}: no BrokerResponse was returned for this request -- missing response.",
        )

    if response.status == STATUS_ROUTED_TO_PAPER:
        if response.fill_information is not None and response.execution_reference is not None:
            return ReconciliationResult(
                request_id=request_id, expected_state=EXPECTED_ROUTED, observed_state=STATUS_ROUTED_TO_PAPER,
                consistency=CONSISTENT,
                explanation=f"{request_id}: routed to PaperBrokerAdapter and a fill was simulated. Reconciliation successful.",
            )
        return ReconciliationResult(
            request_id=request_id, expected_state=EXPECTED_ROUTED, observed_state=STATUS_ROUTED_TO_PAPER,
            consistency=INCONSISTENT,
            explanation=(
                f"{request_id}: status={STATUS_ROUTED_TO_PAPER!r} but fill_information/execution_reference "
                f"is missing -- execution mismatch detected."
            ),
        )

    if response.status == STATUS_SIMULATION_UNAVAILABLE:
        if response.fill_information is None:
            return ReconciliationResult(
                request_id=request_id, expected_state=EXPECTED_UNAVAILABLE, observed_state=STATUS_SIMULATION_UNAVAILABLE,
                consistency=CONSISTENT,
                explanation=f"{request_id}: simulation was correctly not run; no fill_information present. Reconciliation successful.",
            )
        return ReconciliationResult(
            request_id=request_id, expected_state=EXPECTED_UNAVAILABLE, observed_state=STATUS_SIMULATION_UNAVAILABLE,
            consistency=INCONSISTENT,
            explanation=(
                f"{request_id}: status={STATUS_SIMULATION_UNAVAILABLE!r} but fill_information is present -- "
                f"execution mismatch detected."
            ),
        )

    # STATUS_ADAPTER_FAILED -- a genuine adapter-side failure, never fabricated as success.
    return ReconciliationResult(
        request_id=request_id, expected_state=EXPECTED_ROUTED, observed_state=STATUS_ADAPTER_FAILED,
        consistency=INCONSISTENT,
        explanation=f"{request_id}: adapter reported {STATUS_ADAPTER_FAILED!r} -- simulation failure, never silently treated as success.",
    )
