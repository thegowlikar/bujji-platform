"""Production Execution Adapter — BUJJI Options OS v3, Engineering
Series 52, Sprint 1.

Satisfies `bujji.runtime_execution.engine.ExecutionEngineInterface`
(Series 45) using production's own existing
`bujji.execution.engine.ExecutionEngine.submit_and_confirm()`
(identified during the Series 41 architecture review). This adapter
never retries, reconciles, polls, implements idempotency, or
implements broker logic -- all of that already exists inside
`ExecutionEngine` and is reused here, unchanged, exactly as Series 51's
`ProductionAuthenticationAdapter` reused `FyersBroker.connect()`.

`ExecutionResult` is this adapter's own frozen output type. Series 45's
`ExecutionEngineInterface.submit_and_confirm()` was declared with a
generic `Any` return type and no runtime module before this one has
ever defined a concrete result type for it to return -- so, unlike
Series 51 (which reused `AuthenticationOutcome`, a type Series 49
already defined), there is no pre-existing "runtime model" this sprint
can reuse verbatim. `ExecutionResult` is a disclosed, necessary
addition; its `status` values are copied VERBATIM from production's
own `bujji.core.enums.OrderStatus` whenever a real `OrderResult` was
produced -- never invented -- with exactly one exception, documented
below.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from ..core.enums import OptionType, Side
from ..core.models import OptionContract
from ..core.models import OrderRequest as ProductionOrderRequest
from ..execution.engine import ExecutionError
from ..trading_brain.order_construction.models import OrderRequest as RuntimeOrderRequest

Clock = Callable[[], datetime]


def _run_async(coro: Any) -> Any:
    """Run a coroutine from synchronous code -- the same disclosed
    v1 bridging limitation as Series 51's ProductionAuthenticationAdapter:
    raises rather than nesting event loops if called from within one
    already running.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "ProductionExecutionAdapter.submit_and_confirm() cannot be called "
        "from within an already-running event loop in v1 -- call it from "
        "synchronous code."
    )


@dataclass(frozen=True)
class ExecutionResult:
    """This adapter's own frozen output type -- see the module
    docstring for why no pre-existing one could be reused. `status` is
    always one of production's own `OrderStatus` values
    (`PENDING`/`FILLED`/`PARTIAL`/`REJECTED`/`CANCELLED`/`UNKNOWN`) when
    a real `OrderResult` was produced, or the literal string `"FAILED"`
    -- a translation-layer-only marker, never a production
    `OrderStatus` member -- when `ExecutionEngine` raised before ever
    producing one.
    """

    request_id: Optional[str]
    client_order_id: str
    status: str
    broker_order_id: Optional[str]
    filled_quantity: int
    average_price: Optional[float]
    message: str
    execution_trace: str
    timestamp: str
    version: str


def _translate_order_request(
    runtime_order: RuntimeOrderRequest, lot_size: int
) -> ProductionOrderRequest:
    """Translate a broker-neutral runtime OrderRequest (Series 44) into
    production's own OrderRequest/OptionContract shape.

    `lot_size` is supplied explicitly by the caller -- the same
    `LotSpecification.lot_size` value already used by the Position
    Sizing Engine (Series 43) -- since `NiftyOptionContract` (Series
    42) does not itself carry a lot size and this adapter never
    fabricates or infers one.

    `limit_price` and `reference_price` are DELIBERATELY separate here
    (Semantic Cleanup Sprint, correcting a real issue found in the prior
    Live Shadow Real-Time Paper Execution sprint's own micro-review):

    `limit_price` is always `None` -- this pipeline has no path today
    that produces a real, trader-specified LIMIT order (Order
    Construction's own `ExecutionPolicy.limit_price` is not even
    threaded into this function's signature; only `MARKET` orders are
    supported end-to-end, unchanged, disclosed limitation). `None` here
    means exactly one thing to production's own broker layer: submit a
    real MARKET instruction. It is never populated from an observed
    price.

    `reference_price` carries `runtime_order.reference_price` verbatim
    -- the contract's own last observed price, from whatever
    chain/tick snapshot Contract Construction was given. Production's
    real broker layer never reads this field (confirmed: it has zero
    effect on real order type or execution); the paper simulator reads
    it as its own simulated fill price. `None` when no observed price
    was available upstream -- never fabricated by this adapter.
    """
    contract = OptionContract(
        symbol=runtime_order.contract.contract_symbol,
        underlying=runtime_order.contract.underlying,
        strike=runtime_order.contract.strike,
        option_type=OptionType(runtime_order.contract.option_type),
        expiry=runtime_order.contract.expiry,
        lot_size=lot_size,
    )
    tag = f"strategy={runtime_order.tags.strategy_id};session={runtime_order.tags.session_id}"
    return ProductionOrderRequest(
        contract=contract,
        side=Side(runtime_order.side),
        quantity=runtime_order.quantity,
        client_order_id=runtime_order.client_order_id,
        limit_price=None,
        reference_price=runtime_order.reference_price,
        tag=tag,
    )


class ProductionExecutionAdapter:
    """Satisfies `ExecutionEngineInterface` by translating one call
    through an already-constructed production `ExecutionEngine`'s own
    `submit_and_confirm()`.

    Contains no execution logic of its own: no retry, no
    reconciliation, no polling, no idempotency logic, no broker logic.
    Every one of those already exists inside `ExecutionEngine` and is
    reused here, never duplicated.
    """

    def __init__(
        self,
        execution_engine: Any,
        lot_size: int,
        clock: Clock = datetime.now,
    ) -> None:
        self._execution_engine = execution_engine
        self._lot_size = lot_size
        self._clock = clock
        self.last_trace: str = ""

    def submit_and_confirm(self, order_request: RuntimeOrderRequest) -> ExecutionResult:
        """Translate one runtime OrderRequest, hand it to production's
        ExecutionEngine exactly once, and translate the outcome --
        never fabricating success, never suppressing a production
        error, never retrying.
        """
        timestamp = self._clock().isoformat()
        request_trace = (
            f"Runtime request: submit_and_confirm() for client_order_id="
            f"{order_request.client_order_id}."
        )

        production_order = _translate_order_request(order_request, self._lot_size)
        method_trace = "Production method invoked: ExecutionEngine.submit_and_confirm()."

        try:
            result = _run_async(self._execution_engine.submit_and_confirm(production_order))
        except ExecutionError as exc:
            response_trace = f"Production response: ExecutionError raised: {exc}"
            outcome_trace = f"ExecutionResult: FAILED ({exc})."
            trace = " | ".join([request_trace, method_trace, response_trace, outcome_trace])
            self.last_trace = trace
            return ExecutionResult(
                request_id=order_request.request_id,
                client_order_id=order_request.client_order_id,
                status="FAILED",
                broker_order_id=None,
                filled_quantity=0,
                average_price=None,
                message=str(exc),
                execution_trace=trace,
                timestamp=timestamp,
                version="1.0.0",
            )
        except Exception as exc:  # noqa: BLE001 - never suppressed; always surfaced as a failed result
            response_trace = f"Production response: unexpected exception raised: {exc!r}"
            outcome_trace = f"ExecutionResult: FAILED ({exc!r})."
            trace = " | ".join([request_trace, method_trace, response_trace, outcome_trace])
            self.last_trace = trace
            return ExecutionResult(
                request_id=order_request.request_id,
                client_order_id=order_request.client_order_id,
                status="FAILED",
                broker_order_id=None,
                filled_quantity=0,
                average_price=None,
                message=repr(exc),
                execution_trace=trace,
                timestamp=timestamp,
                version="1.0.0",
            )

        response_trace = (
            f"Production response: OrderResult(status={result.status.value}, "
            f"filled_quantity={result.filled_quantity})."
        )
        outcome_trace = f"ExecutionResult: {result.status.value}."
        trace = " | ".join([request_trace, method_trace, response_trace, outcome_trace])
        self.last_trace = trace

        return ExecutionResult(
            request_id=order_request.request_id,
            client_order_id=result.client_order_id,
            status=result.status.value,
            broker_order_id=result.broker_order_id,
            filled_quantity=result.filled_quantity,
            average_price=result.average_price,
            message=result.message,
            execution_trace=trace,
            timestamp=timestamp,
            version="1.0.0",
        )
