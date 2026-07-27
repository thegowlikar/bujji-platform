"""Order Construction Service engine — BUJJI Options OS v3, Engineering
Series 44, Sprint 1 (v1).

The Position Sizing Engine (Series 43) determined exactly what
contracts should be traded and in what quantity. This module converts
that business decision into a complete, broker-neutral order
description -- one `OrderRequest` per contract leg. It never places an
order, authenticates, refreshes a token, opens a socket, retries,
reconciles, polls a broker, or monitors a fill.

Inputs are exactly a `PositionPlan`, an `ExecutionPolicy`, and a
`TradingConfiguration` -- no broker, no Runtime Execution Service, no
ExecutionEngine, no FYERS, no Zerodha.

Construction is atomic: if any validation check fails, zero
OrderRequests are returned -- never a partial, fabricated order set.
This is the last business-logic module in the Trading Brain pipeline.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from ..position_sizing.models import PositionPlan
from . import taxonomy
from .models import ExecutionPolicy, OrderConstructionResult, OrderRequest, OrderTags, TradingConfiguration

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def _failure(
    reason: str,
    position_plan: Optional[PositionPlan],
    timestamp: str,
) -> OrderConstructionResult:
    seed = "|".join([position_plan.plan_id if position_plan else "NONE", reason, timestamp])
    construction_id = "OCR-" + hashlib.md5(seed.encode()).hexdigest()[:16]
    trace = f"FAILED because {reason}."
    return OrderConstructionResult(
        construction_id=construction_id,
        status=taxonomy.CONSTRUCTION_STATUS_FAILED,
        requests=(),
        failure_reason=reason,
        construction_trace=trace,
        position_plan_id=position_plan.plan_id if position_plan else None,
        timestamp=timestamp,
        version=taxonomy.ORDER_CONSTRUCTION_VERSION,
    )


def construct_orders(
    position_plan: Optional[PositionPlan],
    execution_policy: Optional[ExecutionPolicy],
    trading_config: Optional[TradingConfiguration],
    clock: Clock = _real_clock,
) -> OrderConstructionResult:
    """Convert one PositionPlan into one OrderRequest per contract leg.

    Never places an order, never authenticates, never opens a socket,
    never retries, never reconciles, never polls a broker, and never
    monitors a fill -- only constructs a complete, broker-neutral
    order description from data already validated upstream.
    """
    timestamp = clock().isoformat()

    # Rule: nothing usable to construct from at all.
    if position_plan is None or execution_policy is None or trading_config is None:
        return _failure(taxonomy.FAILURE_REASON_INSUFFICIENT_DATA, position_plan, timestamp)

    # Rule: the position plan itself carries nothing to build orders
    # from -- either sizing failed upstream, or the leg set is empty.
    if position_plan.validation != "PASSED" or len(position_plan.contracts) == 0:
        return _failure(taxonomy.FAILURE_REASON_EMPTY_POSITION_PLAN, position_plan, timestamp)

    # Rule: invalid execution policy.
    if execution_policy.policy not in taxonomy.ALL_EXECUTION_POLICIES:
        return _failure(taxonomy.FAILURE_REASON_INVALID_EXECUTION_POLICY, position_plan, timestamp)

    # Rule: invalid product.
    if trading_config.product not in taxonomy.ALL_PRODUCTS:
        return _failure(taxonomy.FAILURE_REASON_INVALID_PRODUCT, position_plan, timestamp)

    # Rule: invalid validity.
    if trading_config.validity not in taxonomy.ALL_VALIDITIES:
        return _failure(taxonomy.FAILURE_REASON_INVALID_VALIDITY, position_plan, timestamp)

    # Rule: zero (or negative) quantity.
    if position_plan.quantity_per_leg <= 0:
        return _failure(taxonomy.FAILURE_REASON_ZERO_QUANTITY, position_plan, timestamp)

    tags = OrderTags(
        strategy_id=position_plan.contracts[0].strategy_id,
        session_id=trading_config.session_id,
        pipeline_version=trading_config.pipeline_version,
        qualification_fingerprint=trading_config.qualification_fingerprint,
    )

    requests: List[OrderRequest] = []
    seen_client_order_ids = set()
    for contract in position_plan.contracts:
        seed = "|".join(
            [position_plan.plan_id, contract.contract_id, execution_policy.policy, timestamp]
        )
        client_order_id = "COID-" + hashlib.md5(seed.encode()).hexdigest()[:16]

        # Rule: duplicate client_order_id -- defensive, should never
        # occur given distinct contract ids, but this module never
        # trusts uniqueness blindly.
        if client_order_id in seen_client_order_ids:
            return _failure(taxonomy.FAILURE_REASON_DUPLICATE_REQUEST, position_plan, timestamp)
        seen_client_order_ids.add(client_order_id)

        request_seed = "|".join([client_order_id, "request", timestamp])
        request_id = "OR-" + hashlib.md5(request_seed.encode()).hexdigest()[:16]

        trace = (
            f"Position Plan {position_plan.plan_id}. Contract {contract.strike}{contract.option_type}. "
            f"Side {contract.side}. Quantity {position_plan.quantity_per_leg}. "
            f"Policy {execution_policy.policy}. Product {trading_config.product}. Request Created."
        )

        requests.append(
            OrderRequest(
                request_id=request_id,
                contract=contract,
                side=contract.side,
                quantity=position_plan.quantity_per_leg,
                order_type=execution_policy.policy,
                product=trading_config.product,
                validity=trading_config.validity,
                execution_policy=execution_policy.policy,
                client_order_id=client_order_id,
                tags=tags,
                creation_trace=trace,
                timestamp=timestamp,
                version=taxonomy.ORDER_CONSTRUCTION_VERSION,
            )
        )

    leg_summary = ", ".join(f"{r.contract.strike}{r.contract.option_type} {r.side}" for r in requests)
    overall_trace = (
        f"Position Plan {position_plan.plan_id}. Constructed {len(requests)} OrderRequest(s): {leg_summary}."
    )
    seed = "|".join([position_plan.plan_id, execution_policy.policy, timestamp])
    construction_id = "OCR-" + hashlib.md5(seed.encode()).hexdigest()[:16]

    return OrderConstructionResult(
        construction_id=construction_id,
        status=taxonomy.CONSTRUCTION_STATUS_CONSTRUCTED,
        requests=tuple(requests),
        failure_reason=None,
        construction_trace=overall_trace,
        position_plan_id=position_plan.plan_id,
        timestamp=timestamp,
        version=taxonomy.ORDER_CONSTRUCTION_VERSION,
    )
