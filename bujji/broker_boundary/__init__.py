"""Phase 20.19 -- Broker Abstraction & Paper Execution Boundary.

Interface Map row: "Execution Intelligence -> Broker Boundary -> Paper
Broker -> Execution Feedback -> Memory." Makes Bujji intelligence
execution-system agnostic: the intelligence layer never knows FYERS,
Dhan, Zerodha, or any broker API/order format. Brokers are replaceable
adapters; Bujji intelligence is permanent.

STEP 1 AUDIT SUMMARY (see docs/PHASE_20_19_BROKER_ABSTRACTION_REPORT.md
for the full audit):

- `bujji.broker.base.Broker` (production broker ABC) -- B) reusable
  PATTERN only. The real "broker-agnostic interface, swappable
  adapters" concept this phase mirrors -- but its own abstract surface
  IS real order-placement vocabulary (`place_order`/`cancel_order`/
  `get_order`/`connect`/`resolve_atm_contract`). `interface.
  BrokerAdapter` below is DELIBERATELY NOT a subclass of `Broker`: no
  implementation of this phase's boundary can be coerced into that
  ABC's own live-order capability.
- `bujji.broker_adapter` (Engineering Series 40, MSI/Trading Brain
  lineage) -- B) reusable pattern only. A real, already-tested
  "translate an already-produced instruction set into broker-neutral
  operation names, never an actual broker call" adapter -- its own
  docstring explicitly avoids importing `bujji.broker`/the FYERS SDK
  chain for exactly the reason this phase also avoids it. Consumes
  `ExecutionInstructionSet` from a different lineage (Series 39), not
  Cycle 1's own `ExecutionIntent`/`ExecutionPlan`. Its "never call a
  broker, only translate" discipline is followed; nothing is imported.
- `bujji.core.execution_adapter` / `bujji.integration.execution_adapter`
  -- C) wrong domain. Real `ExecutionPlan -> OrderRequest` translation
  and real production order submission (`ExecutionEngine.
  submit_and_confirm()`). Not imported.
- `bujji.trading_brain.risk_governor.position_group_fill_reconciliation`
  (Gate A) -- B) reusable pattern only. Real broker cumulative-fill
  reconciliation math, requires real broker-reported quantities Cycle
  1 does not have. Its "expected vs observed, never silently coerce
  a discrepancy" discipline is mirrored in `reconciliation.py`; no
  code is imported.
- `bujji.broker.simulation.{fill_simulator,slippage,market_snapshot}`
  (Gate F.2), `bujji.execution_intelligence` (Phase 20.18) -- A)
  reusable directly. This phase's `PaperBrokerAdapter` calls Phase
  20.18's own `simulate_execution()` directly -- does not reimplement
  any fill/slippage/latency math, and reuses `ExecutionResult`
  directly as `BrokerResponse.fill_information`'s type rather than
  duplicating its fields.

This package NEVER adds `place_order`/`modify_order`/`cancel_order`,
never imports FYERS/Dhan/Zerodha or any live-broker module, never
authenticates to a broker, never creates a real position, and never
deploys capital. The only concrete adapter is `PaperBrokerAdapter`,
whose `adapter_name` is always `"PAPER"` -- never a real broker's
name.
"""
from .explain import explain_broker_response, explain_reconciliation
from .interface import BrokerAdapter, build_execution_request
from .models import (
    ALL_BROKER_RESPONSE_STATUSES, ALL_CONSISTENCY_STATES,
    CONSISTENT, INCONSISTENT, MISSING_RESPONSE,
    STATUS_ADAPTER_FAILED, STATUS_ROUTED_TO_PAPER, STATUS_SIMULATION_UNAVAILABLE,
    BrokerResponse, ExecutionRequest, ReconciliationResult,
)
from .paper_adapter import ADAPTER_NAME, PaperBrokerAdapter
from .reconciliation import reconcile

__all__ = [
    "BrokerAdapter", "PaperBrokerAdapter", "build_execution_request", "reconcile",
    "explain_broker_response", "explain_reconciliation",
    "ExecutionRequest", "BrokerResponse", "ReconciliationResult",
    "ALL_BROKER_RESPONSE_STATUSES", "STATUS_ROUTED_TO_PAPER", "STATUS_SIMULATION_UNAVAILABLE", "STATUS_ADAPTER_FAILED",
    "ALL_CONSISTENCY_STATES", "CONSISTENT", "INCONSISTENT", "MISSING_RESPONSE",
    "ADAPTER_NAME",
]
