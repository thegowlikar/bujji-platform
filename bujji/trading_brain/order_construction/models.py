"""Order Construction Service models — frozen, immutable records.

Nothing here connects to a broker, authenticates, or retries.
`OrderRequest` is a complete, broker-neutral order description -- it
is never submitted by this module, only constructed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from ..nifty_contract_builder.models import NiftyOptionContract


@dataclass(frozen=True)
class ExecutionPolicy:
    policy: str
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    version: str = "1.0.0"


@dataclass(frozen=True)
class TradingConfiguration:
    product: str
    validity: str
    session_id: str
    pipeline_version: str
    qualification_fingerprint: str
    version: str = "1.0.0"


@dataclass(frozen=True)
class OrderTags:
    strategy_id: str
    session_id: str
    pipeline_version: str
    qualification_fingerprint: str


@dataclass(frozen=True)
class OrderRequest:
    request_id: str
    contract: NiftyOptionContract
    side: str
    quantity: int
    order_type: str
    product: str
    validity: str
    execution_policy: str
    client_order_id: str
    tags: OrderTags
    creation_trace: str
    timestamp: str
    version: str
    # Live Shadow Real-Time Paper Execution sprint: the contract's own
    # last_price, carried forward verbatim (never re-derived here) so a
    # downstream simulated/paper fill can use the real observed market
    # price instead of a synthetic default. None when the upstream
    # chain/contract carried no observed price -- never defaulted here.
    reference_price: Optional[float] = None


@dataclass(frozen=True)
class OrderConstructionResult:
    construction_id: str
    status: str
    requests: Tuple[OrderRequest, ...]
    failure_reason: Optional[str]
    construction_trace: str
    position_plan_id: Optional[str]
    timestamp: str
    version: str
