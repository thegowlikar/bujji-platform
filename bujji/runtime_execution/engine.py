"""Runtime Execution Orchestrator engine — BUJJI Options OS v3,
Engineering Series 45, Sprint 1 (v1).

This module is an orchestrator. It owns workflow -- validating
execution readiness and preparing deterministic dispatch sequencing --
and owns nothing else. It owns no broker logic, no retry logic, no
authentication, and no reconciliation; those remain entirely inside
production's own `Broker` and `ExecutionEngine`, identified during the
Series 41 architecture review and never imported, modified, or
shadowed here.

`ExecutionEngineInterface` is a structural `typing.Protocol` -- an
abstract shape, not an import of production's concrete
`bujji.execution.engine.ExecutionEngine`. This module never
instantiates a broker SDK and never performs network I/O; any actual
I/O happens entirely inside whatever object satisfies this Protocol,
injected from outside this package.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Callable, List, Optional, Protocol, Tuple, runtime_checkable

from ..trading_brain.order_construction.models import OrderRequest
from . import taxonomy
from .models import DispatchInstruction, ExecutionSession

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


@runtime_checkable
class ExecutionEngineInterface(Protocol):
    """The minimal shape this orchestrator needs from a production
    execution engine. Production's own `bujji.execution.engine.ExecutionEngine`
    already satisfies this shape (`submit_and_confirm(order_request)`)
    -- this Protocol is never a replacement for it, only a seam this
    module can be tested against without importing it.
    """

    def submit_and_confirm(self, order_request: Any) -> Any: ...


def _session_id(order_requests: Optional[Tuple[OrderRequest, ...]], state: str, timestamp: str) -> str:
    ids = tuple(o.client_order_id for o in order_requests) if order_requests else ()
    seed = "|".join(["|".join(ids), state, timestamp])
    return "ES-" + hashlib.md5(seed.encode()).hexdigest()[:16]


def _failure(
    reason: str,
    order_requests: Tuple[OrderRequest, ...],
    state: str,
    timestamp: str,
) -> ExecutionSession:
    trace = f"{state} because {reason}."
    return ExecutionSession(
        session_id=_session_id(order_requests, state, timestamp),
        order_requests=order_requests,
        execution_state=state,
        dispatch_plan=(),
        validation_result=taxonomy.VALIDATION_RESULT_FAILED,
        execution_trace=trace,
        failure_reason=reason,
        timestamp=timestamp,
        version=taxonomy.RUNTIME_EXECUTION_VERSION,
    )


def _order_request_is_valid(order_request: OrderRequest) -> bool:
    if order_request.contract is None:
        return False
    if order_request.quantity <= 0:
        return False
    if order_request.order_type not in taxonomy.REUSED_ORDER_TYPES:
        return False
    if not order_request.client_order_id:
        return False
    return True


def build_session(
    order_requests: Optional[Tuple[OrderRequest, ...]],
    clock: Clock = _real_clock,
) -> ExecutionSession:
    """Validate a set of OrderRequests and prepare a deterministic
    dispatch plan -- one instruction per request, never fabricated.

    Never authenticates, never retries, never polls, never reconciles,
    never performs network I/O. Construction is atomic: if validation
    fails for any single OrderRequest, zero dispatch instructions are
    created.
    """
    timestamp = clock().isoformat()

    # Rule: nothing supplied at all.
    if order_requests is None:
        return _failure(
            taxonomy.FAILURE_REASON_INSUFFICIENT_DATA, (), taxonomy.STATE_ABORTED, timestamp
        )

    # Rule: an empty session.
    if len(order_requests) == 0:
        return _failure(
            taxonomy.FAILURE_REASON_EMPTY_SESSION, (), taxonomy.STATE_FAILED_VALIDATION, timestamp
        )

    # Rule: every OrderRequest must itself be well-formed.
    if not all(_order_request_is_valid(o) for o in order_requests):
        return _failure(
            taxonomy.FAILURE_REASON_INVALID_ORDER_REQUEST,
            order_requests,
            taxonomy.STATE_FAILED_VALIDATION,
            timestamp,
        )

    # Rule: client_order_ids must be unique within the session.
    ids = [o.client_order_id for o in order_requests]
    if len(set(ids)) != len(ids):
        return _failure(
            taxonomy.FAILURE_REASON_DUPLICATE_CLIENT_ORDER_ID,
            order_requests,
            taxonomy.STATE_FAILED_VALIDATION,
            timestamp,
        )

    dispatch_plan = tuple(
        DispatchInstruction(sequence=i, client_order_id=o.client_order_id, order_request=o)
        for i, o in enumerate(order_requests)
    )

    # Defensive: the dispatch plan must be exactly 1:1 with the
    # validated OrderRequests. Structurally guaranteed by construction
    # above, but this module never trusts its own output blindly
    # either.
    if len(dispatch_plan) != len(order_requests):
        return _failure(
            taxonomy.FAILURE_REASON_INVALID_DISPATCH_PLAN,
            order_requests,
            taxonomy.STATE_FAILED_VALIDATION,
            timestamp,
        )

    leg_summary = ", ".join(
        f"{d.order_request.contract.strike}{d.order_request.contract.option_type}" for d in dispatch_plan
    )
    trace = (
        f"OrderRequest x{len(order_requests)}. Validation PASSED. "
        f"Dispatch Plan: {leg_summary}. READY."
    )

    return ExecutionSession(
        session_id=_session_id(order_requests, taxonomy.STATE_READY, timestamp),
        order_requests=order_requests,
        execution_state=taxonomy.STATE_READY,
        dispatch_plan=dispatch_plan,
        validation_result=taxonomy.VALIDATION_RESULT_PASSED,
        execution_trace=trace,
        failure_reason=None,
        timestamp=timestamp,
        version=taxonomy.RUNTIME_EXECUTION_VERSION,
    )


def queue_for_dispatch(session: ExecutionSession, clock: Clock = _real_clock) -> ExecutionSession:
    """Transition a READY session to DISPATCH_PENDING.

    A no-op (returns the session unchanged) if it is not currently
    READY -- this function never forces an invalid transition.
    """
    if session.execution_state != taxonomy.STATE_READY:
        return session

    timestamp = clock().isoformat()
    trace = session.execution_trace + " Queued for dispatch. DISPATCH_PENDING."
    return ExecutionSession(
        session_id=session.session_id,
        order_requests=session.order_requests,
        execution_state=taxonomy.STATE_DISPATCH_PENDING,
        dispatch_plan=session.dispatch_plan,
        validation_result=session.validation_result,
        execution_trace=trace,
        failure_reason=None,
        timestamp=timestamp,
        version=session.version,
    )


def dispatch(
    session: ExecutionSession,
    executor: ExecutionEngineInterface,
    clock: Clock = _real_clock,
) -> ExecutionSession:
    """Hand every dispatch instruction to an injected
    ExecutionEngineInterface, in order.

    A no-op (returns the session unchanged) if it is not currently
    DISPATCH_PENDING. This function contains no I/O of its own -- it
    only calls `executor.submit_and_confirm()` once per instruction.
    Whatever `executor` actually does (a real production
    `ExecutionEngine`, or a test stub) is entirely outside this
    module's control and import graph. If `executor` raises for any
    instruction, the session becomes ABORTED -- this module makes no
    claim about broker-side state after a partial sequence, since it
    performs no reconciliation of its own.
    """
    if session.execution_state != taxonomy.STATE_DISPATCH_PENDING:
        return session

    timestamp = clock().isoformat()

    try:
        for instruction in session.dispatch_plan:
            executor.submit_and_confirm(instruction.order_request)
    except Exception:  # noqa: BLE001 - deliberately broad; reported via state, not swallowed
        trace = session.execution_trace + " Dispatch raised. ABORTED."
        return ExecutionSession(
            session_id=session.session_id,
            order_requests=session.order_requests,
            execution_state=taxonomy.STATE_ABORTED,
            dispatch_plan=session.dispatch_plan,
            validation_result=session.validation_result,
            execution_trace=trace,
            failure_reason=None,
            timestamp=timestamp,
            version=session.version,
        )

    trace = session.execution_trace + " Dispatched to ExecutionEngineInterface. DISPATCHED."
    return ExecutionSession(
        session_id=session.session_id,
        order_requests=session.order_requests,
        execution_state=taxonomy.STATE_DISPATCHED,
        dispatch_plan=session.dispatch_plan,
        validation_result=session.validation_result,
        execution_trace=trace,
        failure_reason=None,
        timestamp=timestamp,
        version=session.version,
    )
