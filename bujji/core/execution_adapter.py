"""Execution Adapter — Production Pipeline Entry 11, boundary between Order
Planning and the Broker Layer.

Purely mechanical translation, ExecutionPlan -> Broker OrderRequest(s), in
execution_sequence order. No strategy, no intelligence, no risk, no
learning -- this module imports only OrderRequest/Side and touches nothing
else, by design, so it can never accidentally acquire any of those
responsibilities.
"""
from __future__ import annotations

from .models import ExecutionPlan, OrderRequest


def translate(plan: ExecutionPlan) -> list[OrderRequest]:
    """Returns OrderRequests in `plan.execution_sequence` order -- e.g. CE
    before PE for a straddle, matching the exact submission order the
    pre-Sprint-2 inline code used (load-bearing: the partial-leg-unwind
    logic in Orchestrator._enter() unwinds whichever leg filled first if
    the second fails, so sequence order is not arbitrary).
    """
    return [
        OrderRequest(
            contract=plan.contracts[leg],
            side=plan.side_per_leg[leg],
            quantity=plan.quantities[leg],
            client_order_id=plan.idempotency_keys[leg],
            tag=f"entry_{leg}",
        )
        for leg in plan.execution_sequence
    ]
