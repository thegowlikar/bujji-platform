"""Phase 20.18 -- Execution Intelligence Layer.

Interface Map row: "Risk Context Adapter -> Execution Intelligence ->
Shadow Result -> Market Memory." Answers "if Bujji decided to act,
what would happen in a realistic PAPER execution environment?" --
never live execution, never broker integration, never order
placement, never capital deployment.

STEP 1 AUDIT SUMMARY (see docs/PHASE_20_18_EXECUTION_INTELLIGENCE_REPORT.md
for the full audit):

- `bujji.broker.simulation.fill_simulator.FillSimulator` (Gate F.2
  Part 1) -- A) reusable directly. Real, already-tested, deterministic
  (given an injected `random.Random`), broker-independent fill
  simulation. Imports only `bujji.core.enums.OrderStatus` -- zero
  broker/fyers/dhan imports. Reused via composition, never
  reimplemented.
- `bujji.broker.simulation.slippage.SlippageCalculator` (Gate F.2 Part
  2) -- A) reusable directly. Pure, deterministic, zero broker
  imports. Reused via `FillSimulator`, never called or reimplemented
  independently.
- `bujji.broker.simulation.market_snapshot.MarketSnapshot` (Gate F.2
  Part 1) -- A) reusable directly as this layer's own injected-input
  contract. Never fabricated internally by this package -- always
  caller-supplied.
- `bujji.broker.simulation.order_lifecycle.OrderLifecycleTracker` --
  B) reusable pattern only. A real, order-scoped state machine for a
  MULTI-STAGE simulated order; this phase's own `ExecutionResult` is a
  single terminal outcome per simulated attempt (no multi-stage
  tracking need), so the tracker itself is not imported, but its
  "small, order-scoped, does not duplicate the runtime state machine"
  discipline is followed.
- `bujji.execution_backtest` (Phase 20.2) -- B) reusable pattern only.
  Real, already-tested composition of `FillSimulator` for HISTORICAL
  BATCH backtesting, computing real `quantity`/`net_pnl`/`fees` --
  explicitly execution/capital territory this phase's own boundary
  forbids (no quantity sizing, no P&L, no capital deployment). Its
  "compose FillSimulator, never reimplement fill/slippage math" pattern
  is followed; its P&L/quantity computation is not imported.
- `bujji.trading_brain.execution_planner`/`execution_engine` (MSI/
  Trading Brain lineage) -- B) reusable pattern only, wrong domain.
  Real, broker-independent, deterministic "conceptual workflow steps"
  planners -- but consume `CapitalDecision`/`StrategyDecision` from a
  DIFFERENT lineage (Capital Brain/Strategy Selector), not Cycle 1's
  own `FinalDecision`/`RiskContextAssessment`. DISCLOSED NAME
  COLLISION: `execution_planner.models.ExecutionPlan` is a real,
  separate class in a separate package -- this phase's own
  `execution_intelligence.models.ExecutionPlan` is never imported
  from, or interchanged with, it (same precedent as every prior
  disclosed collision in this engagement).
- `bujji.execution_reality` (Phase 17F lineage) -- C) wrong domain.
  Real broker quote/liquidity data for option-chain premium discovery.
  Not imported.
- `bujji.decision_orchestration.FinalDecision` (Phase 20.10),
  `bujji.risk_context_adapter.RiskContextAssessment` (Phase 20.17.1)
  -- A) reusable directly as this layer's inputs. Never recomputed,
  never modified.

This package NEVER adds a broker connection, places an order, creates
a real position, computes a real position size, or deploys capital.
Every simulated fill uses a fixed notional quantity (1 unit, matching
Phase 20.17's own "notional probe" precedent) -- never a real proposed
order size.
"""
from .analytics import assess_execution_quality
from .explain import explain_execution_intent, explain_execution_result, explain_no_execution_intent
from .models import (
    ALL_EXECUTION_STATUSES, ALL_FILL_QUALITIES,
    QUALITY_DEGRADED, QUALITY_GOOD, QUALITY_REJECTED,
    STATUS_FILLED, STATUS_PARTIAL, STATUS_REJECTED,
    ExecutionIntent, ExecutionPlan, ExecutionQualityAssessment, ExecutionResult,
)
from .planner import build_execution_intent, build_execution_plan
from .simulator import simulate_execution, simulation_permitted

__all__ = [
    "build_execution_intent", "build_execution_plan", "simulate_execution", "simulation_permitted",
    "assess_execution_quality", "explain_execution_intent", "explain_execution_result", "explain_no_execution_intent",
    "ExecutionIntent", "ExecutionPlan", "ExecutionResult", "ExecutionQualityAssessment",
    "ALL_EXECUTION_STATUSES", "ALL_FILL_QUALITIES",
    "STATUS_FILLED", "STATUS_PARTIAL", "STATUS_REJECTED",
    "QUALITY_GOOD", "QUALITY_DEGRADED", "QUALITY_REJECTED",
]
