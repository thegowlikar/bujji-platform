"""Phase 20.19 -- the abstract broker boundary. Interfaces only. No
implementation, no live broker knowledge.

DELIBERATELY NOT a subclass of `bujji.broker.base.Broker`: that ABC's
own abstract surface (`connect`, `place_order`, `get_order`,
`cancel_order`, `resolve_atm_contract`, ...) is real ORDER-placement
vocabulary -- subclassing it would obligate every implementation of
THIS boundary to implement those methods too, which is exactly the
live-order capability this phase must never introduce. `BrokerAdapter`
below is a narrower, disclosed, separate interface scoped to exactly
what Execution Intelligence needs: submit a request, poll a status,
reconcile a result. Nothing here can be coerced into the production
`Broker` ABC's own capability surface.
"""
from __future__ import annotations

import abc
from datetime import datetime
from typing import Optional

from bujji.execution_intelligence import ExecutionPlan
from bujji.broker.simulation.market_snapshot import MarketSnapshot

from .models import BrokerResponse, ExecutionRequest, ReconciliationResult


def build_execution_request(intent, risk_assessment, *, timestamp: datetime) -> ExecutionRequest:
    """Pure translation from `bujji.execution_intelligence.
    ExecutionIntent` -- reads only already-computed fields, never
    recomputes evidence/confidence/allocation."""
    return ExecutionRequest(
        execution_intent_id=intent.decision_id, strategy_name=intent.strategy_name,
        direction=intent.direction, expected_behavior=intent.expected_behavior,
        risk_context_status=risk_assessment.status, timestamp=timestamp,
    )


class BrokerAdapter(abc.ABC):
    """Minimal surface any execution-boundary adapter must implement.
    Swapping the paper adapter for a different execution backend
    requires only a new subclass -- no change to Execution
    Intelligence or anything upstream of it."""

    name: str = "base"

    @abc.abstractmethod
    def submit_execution_request(
        self, request: ExecutionRequest, plan: ExecutionPlan, snapshot: MarketSnapshot, *, seed: int = 0,
    ) -> BrokerResponse:
        """Route a request through this adapter's own execution
        backend. Never places a real order; never blocks on live
        network I/O by design (a paper adapter's own contract)."""

    @abc.abstractmethod
    def get_execution_status(self, execution_reference: str) -> Optional[BrokerResponse]:
        """Poll a previously submitted execution's current status, if
        this adapter tracks state at all. Returns `None` when the
        adapter is stateless or the reference is unknown -- never
        fabricated."""

    @abc.abstractmethod
    def reconcile_execution(
        self, request: ExecutionRequest, response: Optional[BrokerResponse],
    ) -> ReconciliationResult:
        """Compare what was requested against what this adapter
        actually returned."""
