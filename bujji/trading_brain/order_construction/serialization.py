"""JSON round-trip for OrderConstructionResult / OrderRequest and the
ExecutionPolicy / TradingConfiguration input types.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..nifty_contract_builder.serialization import contract_from_dict, contract_to_dict
from .models import (
    ExecutionPolicy,
    OrderConstructionResult,
    OrderRequest,
    OrderTags,
    TradingConfiguration,
)


def execution_policy_to_dict(p: ExecutionPolicy) -> Dict[str, Any]:
    return {
        "policy": p.policy,
        "limit_price": p.limit_price,
        "stop_price": p.stop_price,
        "version": p.version,
    }


def execution_policy_from_dict(d: Dict[str, Any]) -> ExecutionPolicy:
    return ExecutionPolicy(
        policy=d["policy"],
        limit_price=d.get("limit_price"),
        stop_price=d.get("stop_price"),
        version=d["version"],
    )


def trading_configuration_to_dict(c: TradingConfiguration) -> Dict[str, Any]:
    return {
        "product": c.product,
        "validity": c.validity,
        "session_id": c.session_id,
        "pipeline_version": c.pipeline_version,
        "qualification_fingerprint": c.qualification_fingerprint,
        "version": c.version,
    }


def trading_configuration_from_dict(d: Dict[str, Any]) -> TradingConfiguration:
    return TradingConfiguration(
        product=d["product"],
        validity=d["validity"],
        session_id=d["session_id"],
        pipeline_version=d["pipeline_version"],
        qualification_fingerprint=d["qualification_fingerprint"],
        version=d["version"],
    )


def tags_to_dict(t: OrderTags) -> Dict[str, Any]:
    return {
        "strategy_id": t.strategy_id,
        "session_id": t.session_id,
        "pipeline_version": t.pipeline_version,
        "qualification_fingerprint": t.qualification_fingerprint,
    }


def tags_from_dict(d: Dict[str, Any]) -> OrderTags:
    return OrderTags(
        strategy_id=d["strategy_id"],
        session_id=d["session_id"],
        pipeline_version=d["pipeline_version"],
        qualification_fingerprint=d["qualification_fingerprint"],
    )


def order_request_to_dict(r: OrderRequest) -> Dict[str, Any]:
    return {
        "request_id": r.request_id,
        "contract": contract_to_dict(r.contract),
        "side": r.side,
        "quantity": r.quantity,
        "order_type": r.order_type,
        "product": r.product,
        "validity": r.validity,
        "execution_policy": r.execution_policy,
        "client_order_id": r.client_order_id,
        "tags": tags_to_dict(r.tags),
        "creation_trace": r.creation_trace,
        "timestamp": r.timestamp,
        "version": r.version,
    }


def order_request_from_dict(d: Dict[str, Any]) -> OrderRequest:
    return OrderRequest(
        request_id=d["request_id"],
        contract=contract_from_dict(d["contract"]),
        side=d["side"],
        quantity=d["quantity"],
        order_type=d["order_type"],
        product=d["product"],
        validity=d["validity"],
        execution_policy=d["execution_policy"],
        client_order_id=d["client_order_id"],
        tags=tags_from_dict(d["tags"]),
        creation_trace=d["creation_trace"],
        timestamp=d["timestamp"],
        version=d["version"],
    )


def result_to_dict(r: OrderConstructionResult) -> Dict[str, Any]:
    return {
        "construction_id": r.construction_id,
        "status": r.status,
        "requests": [order_request_to_dict(o) for o in r.requests],
        "failure_reason": r.failure_reason,
        "construction_trace": r.construction_trace,
        "position_plan_id": r.position_plan_id,
        "timestamp": r.timestamp,
        "version": r.version,
    }


def result_from_dict(d: Dict[str, Any]) -> OrderConstructionResult:
    requests: List[OrderRequest] = [order_request_from_dict(o) for o in d["requests"]]
    return OrderConstructionResult(
        construction_id=d["construction_id"],
        status=d["status"],
        requests=tuple(requests),
        failure_reason=d.get("failure_reason"),
        construction_trace=d["construction_trace"],
        position_plan_id=d.get("position_plan_id"),
        timestamp=d["timestamp"],
        version=d["version"],
    )
