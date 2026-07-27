"""JSON round-trip for ExecutionSession / DispatchInstruction."""
from __future__ import annotations

from typing import Any, Dict, List

from ..trading_brain.order_construction.serialization import (
    order_request_from_dict,
    order_request_to_dict,
)
from .models import DispatchInstruction, ExecutionSession


def dispatch_instruction_to_dict(d: DispatchInstruction) -> Dict[str, Any]:
    return {
        "sequence": d.sequence,
        "client_order_id": d.client_order_id,
        "order_request": order_request_to_dict(d.order_request),
    }


def dispatch_instruction_from_dict(d: Dict[str, Any]) -> DispatchInstruction:
    return DispatchInstruction(
        sequence=d["sequence"],
        client_order_id=d["client_order_id"],
        order_request=order_request_from_dict(d["order_request"]),
    )


def session_to_dict(s: ExecutionSession) -> Dict[str, Any]:
    return {
        "session_id": s.session_id,
        "order_requests": [order_request_to_dict(o) for o in s.order_requests],
        "execution_state": s.execution_state,
        "dispatch_plan": [dispatch_instruction_to_dict(d) for d in s.dispatch_plan],
        "validation_result": s.validation_result,
        "execution_trace": s.execution_trace,
        "failure_reason": s.failure_reason,
        "timestamp": s.timestamp,
        "version": s.version,
    }


def session_from_dict(d: Dict[str, Any]) -> ExecutionSession:
    order_requests = [order_request_from_dict(o) for o in d["order_requests"]]
    dispatch_plan: List[DispatchInstruction] = [
        dispatch_instruction_from_dict(x) for x in d["dispatch_plan"]
    ]
    return ExecutionSession(
        session_id=d["session_id"],
        order_requests=tuple(order_requests),
        execution_state=d["execution_state"],
        dispatch_plan=tuple(dispatch_plan),
        validation_result=d["validation_result"],
        execution_trace=d["execution_trace"],
        failure_reason=d.get("failure_reason"),
        timestamp=d["timestamp"],
        version=d["version"],
    )
