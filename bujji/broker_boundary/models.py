"""Phase 20.19 -- pure data contracts. No broker objects, no API
fields, no order-placement vocabulary anywhere in this module.

`fill_information` reuses `bujji.execution_intelligence.ExecutionResult`
directly (Phase 20.18's own type) rather than duplicating its fields --
the same "reuse, never re-derive" discipline as every prior phase.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from bujji.execution_intelligence import ExecutionResult

# -- BrokerResponse.status -----------------------------------------------
STATUS_ROUTED_TO_PAPER = "ROUTED_TO_PAPER"
STATUS_SIMULATION_UNAVAILABLE = "SIMULATION_UNAVAILABLE"
STATUS_ADAPTER_FAILED = "ADAPTER_FAILED"
ALL_BROKER_RESPONSE_STATUSES = (STATUS_ROUTED_TO_PAPER, STATUS_SIMULATION_UNAVAILABLE, STATUS_ADAPTER_FAILED)

# -- ReconciliationResult.consistency ------------------------------------
CONSISTENT = "CONSISTENT"
INCONSISTENT = "INCONSISTENT"
MISSING_RESPONSE = "MISSING_RESPONSE"
ALL_CONSISTENCY_STATES = (CONSISTENT, INCONSISTENT, MISSING_RESPONSE)


@dataclass(frozen=True)
class ExecutionRequest:
    """A boundary-crossing REQUEST -- never an order, never a broker
    payload. `direction` is honestly `None`: Cycle 1 never computes a
    directional entry side (same disclosed gap as `ExecutionIntent.
    direction`, Phase 20.18)."""

    execution_intent_id: str
    strategy_name: str
    direction: Optional[str]
    expected_behavior: str
    risk_context_status: str
    timestamp: datetime


@dataclass(frozen=True)
class BrokerResponse:
    """The ONLY output any `BrokerAdapter` implementation may produce.
    `adapter_name` identifies which adapter answered (e.g. `"PAPER"`)
    -- never a broker's own name (FYERS/Dhan/Zerodha never appear
    anywhere in this package). `execution_reference` is a paper-only
    identifier, never an exchange order id."""

    adapter_name: str
    status: str
    execution_reference: Optional[str]
    fill_information: Optional[ExecutionResult]
    timestamp: datetime

    def __post_init__(self) -> None:
        if self.status not in ALL_BROKER_RESPONSE_STATUSES:
            raise ValueError(f"status={self.status!r} not in {ALL_BROKER_RESPONSE_STATUSES}")


@dataclass(frozen=True)
class ReconciliationResult:
    """Compares what Bujji asked for against what the adapter actually
    returned -- never a broker-side reconciliation (no real fill
    quantities, no real cumulative figures)."""

    request_id: str
    expected_state: str
    observed_state: str
    consistency: str
    explanation: str

    def __post_init__(self) -> None:
        if self.consistency not in ALL_CONSISTENCY_STATES:
            raise ValueError(f"consistency={self.consistency!r} not in {ALL_CONSISTENCY_STATES}")
