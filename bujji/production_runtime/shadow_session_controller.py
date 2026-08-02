"""Shadow Session Controller -- BUJJI Options OS v3, Gate F.5.

PURPOSE: the conductor. Owns ONE trading session and sequences
already-proven components (F.1 TradingBrainRuntime, F.3 Portfolio
Reality Engine + Position Lifecycle Runtime, F.4 Trade Lifecycle
Executor) through a deterministic market-day lifecycle, driven by
F.0's own RuntimeStateMachine. This module makes NO trading, risk, or
exit decision of any kind -- every decision-shaped return value it
touches was already produced by the component that owns that
decision; this controller only calls that component, in the right
order, and records what happened.

AUTHORITY MAP (no overlapping ownership):
  Session lifecycle    -> F.0 RuntimeStateMachine (this module only calls
                           TradingBrainRuntime.run_market_open_sequence()/
                           run_market_close_sequence(), never transitions
                           RuntimeState directly itself).
  Market state          -> caller-supplied (this controller has no feed
                           of its own -- market_feed_status/latest_prices/
                           chain/spot are always passed in).
  Entry decisions          -> Strategy Engine + D.1-D.6 (via F.1's
                           TradingBrainRuntime.process_entry_cycle(),
                           called verbatim, never re-implemented).
  Position valuation        -> Portfolio Reality Engine (F.3), called
                           verbatim.
  Lifecycle decisions         -> D.4, via PositionLifecycleRuntime.
                           evaluate_group() (F.3), called verbatim.
  Order execution               -> PaperBroker, via F.1's dispatch bridge
                           for entries and F.4's TradeLifecycleExecutor
                           for reduce/hedge -- this controller never
                           calls broker.place_order() itself.
  EOD shutdown                    -> this controller's own
                           run_eod_reconciliation(), but it never force-
                           closes a position (no existing component owns
                           that decision, so none is invented here).

LEGACY RUNTIME UNTOUCHED: this module never imports `production_runtime.
runtime`/`composition_root`/`run_shadow` or any of the 15 legacy stage
engines -- verified by this module's own test suite, matching every
prior Gate F module's own discipline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot
from bujji.trading_brain.risk_governor.position_lifecycle_intelligence import PositionHealthThresholds
from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import PortfolioRiskThresholds

from .position_lifecycle_runtime import LifecycleEvaluationResult, PositionLifecycleRuntime
from .position_reality_registry import PositionRealityRegistry
from .portfolio_reality_engine import PortfolioRealityEngine
from .runtime_scheduler import RuntimeScheduler
from .session_models import EODReconciliationResult, SessionHeartbeat, generate_session_summary
from .shadow_trade_timeline import ShadowTradeTimeline
from .trade_lifecycle_executor import LifecycleExecutionResult, TradeLifecycleExecutor
from .trading_brain_composition_root import TradingBrainCompositionRoot
from .trading_brain_runtime import TradingBrainCycleResult, TradingBrainRuntime

Clock = Callable[[], datetime]

TASK_PORTFOLIO_REFRESH = "PORTFOLIO_REFRESH"
TASK_LIFECYCLE_REFRESH = "LIFECYCLE_REFRESH"
TASK_HEARTBEAT = "HEARTBEAT"


@dataclass(frozen=True)
class ManagementCycleResult:
    valuations: Dict[str, object]
    lifecycle_evaluations: Tuple[LifecycleEvaluationResult, ...]
    execution_results: Tuple[LifecycleExecutionResult, ...]


class ShadowSessionController:
    def __init__(
        self,
        root: TradingBrainCompositionRoot,
        trading_brain_runtime: TradingBrainRuntime,
        registry: PositionRealityRegistry,
        portfolio_engine: PortfolioRealityEngine,
        lifecycle_runtime: PositionLifecycleRuntime,
        executor: TradeLifecycleExecutor,
        scheduler: RuntimeScheduler,
        timeline: ShadowTradeTimeline,
        clock: Clock,
        observatory: Optional[object] = None,
    ) -> None:
        self._root = root
        self._trading_brain_runtime = trading_brain_runtime
        self._registry = registry
        self._portfolio_engine = portfolio_engine
        self._lifecycle_runtime = lifecycle_runtime
        self._executor = executor
        self._scheduler = scheduler
        self._timeline = timeline
        self._clock = clock
        self._last_decision_timestamp: Optional[datetime] = None
        self._last_execution_timestamp: Optional[datetime] = None
        # Gate V.0's own integration boundary: EventBus -> Observatory
        # (wired by the caller attaching the recorder to root.event_bus
        # before constructing this controller) plus these two direct
        # calls -- session start/end and heartbeat snapshots, since a
        # heartbeat is a pulled snapshot no component ever publishes as
        # a domain event. `observatory` is Optional and untyped here on
        # purpose: this controller stays unaware of the Observatory's
        # own class, only calling two documented methods on it if
        # supplied.
        self._observatory = observatory

    # ------------------------------------------------------------------ #
    # Session lifecycle -- delegates entirely to F.1's own sequences,
    # which themselves drive F.0's RuntimeStateMachine. Never transitions
    # RuntimeState directly.
    # ------------------------------------------------------------------ #

    def start_session(self, manifest: Optional[object] = None) -> None:
        """`manifest`: an optional Gate V.0 `SessionManifest` -- the
        evidence-chain identity card (code_version/config_hash/
        strategy_engine/risk_engine/...). This controller never
        constructs one itself (it doesn't know its own git commit or
        config hash) -- it only forwards whatever the caller supplies
        to the Observatory, verbatim."""
        self._trading_brain_runtime.run_market_open_sequence()
        if self._observatory is not None:
            self._observatory.record_session_start(self._clock(), self._root.runtime_state_machine.state.value)
            if manifest is not None:
                self._observatory.record_session_manifest(manifest)

    def shutdown(self) -> None:
        pass  # No broker connection/resource to release for PaperBroker -- symmetry with F.1's own shutdown discipline.

    # ------------------------------------------------------------------ #
    # Entry cycle -- pure delegation to F.1.
    # ------------------------------------------------------------------ #

    def run_entry_cycle(self, **entry_kwargs) -> TradingBrainCycleResult:
        result = self._trading_brain_runtime.process_entry_cycle(**entry_kwargs)
        self._last_decision_timestamp = self._clock()
        if result.filled:
            self._last_execution_timestamp = self._clock()
        return result

    def register_filled_entry(
        self, position_group_id: str, strategy_family: str, symbols: List[str],
        initial_risk: float, contracts: Optional[Dict[str, object]] = None,
    ) -> None:
        """Mechanical bookkeeping only -- registers a just-filled entry
        into the Position Reality Registry (F.3's own registration
        method, called verbatim) and marks it ENTERING/OPEN on the
        Position Lifecycle Runtime (F.3's own tracker, called verbatim).
        No decision is made here."""
        self._registry.register_entry(position_group_id, strategy_family, symbols, initial_risk, self._clock,
                                       contracts=contracts)
        self._lifecycle_runtime.mark_entering(position_group_id)
        self._lifecycle_runtime.mark_open(position_group_id)

    # ------------------------------------------------------------------ #
    # Management cycle -- pure sequencing of F.3 (valuation + risk
    # refresh) then F.3's own D.4 call then F.4 (execution).
    # ------------------------------------------------------------------ #

    async def run_management_cycle(
        self, latest_prices: Dict[str, float], capital_snapshot: CapitalSafetySnapshot,
        margin_snapshot: Optional[object], margin_explanation: Optional[object], position_groups: list,
        position_health_thresholds: Optional[PositionHealthThresholds],
        portfolio_risk_thresholds: Optional[PortfolioRiskThresholds],
        reduce_quantities: Optional[Dict[str, int]] = None,
        hedge_instructions: Optional[Dict[str, dict]] = None,
    ) -> ManagementCycleResult:
        valuations = await self._portfolio_engine.revalue_all(latest_prices, self._clock)
        risk_context = await self._portfolio_engine.refresh_risk_context(
            capital_snapshot, None, portfolio_risk_thresholds, margin_snapshot, margin_explanation,
            position_groups, self._clock,
        )

        evaluations: List[LifecycleEvaluationResult] = []
        execution_results: List[LifecycleExecutionResult] = []
        for position_group_id, valuation in valuations.items():
            reality = await self._registry.get_group_reality(position_group_id)
            evaluation = await self._lifecycle_runtime.evaluate_group(
                position_group_id, valuation, reality.strategy_family, risk_context.capital_status,
                risk_context.portfolio_status, position_health_thresholds, self._clock,
            )
            evaluations.append(evaluation)
            self._last_decision_timestamp = self._clock()

            exec_result = await self._executor.execute(
                evaluation, self._clock,
                reduce_quantity=(reduce_quantities or {}).get(position_group_id),
                hedge_instruction=(hedge_instructions or {}).get(position_group_id),
            )
            execution_results.append(exec_result)
            if exec_result.orders_submitted:
                self._last_execution_timestamp = self._clock()

        return ManagementCycleResult(
            valuations=valuations, lifecycle_evaluations=tuple(evaluations),
            execution_results=tuple(execution_results),
        )

    # ------------------------------------------------------------------ #
    # Heartbeat -- pure observation, no decision.
    # ------------------------------------------------------------------ #

    async def heartbeat(self, market_feed_status: str = "UNKNOWN") -> SessionHeartbeat:
        open_groups = await self._registry.open_group_ids()
        hb = SessionHeartbeat(
            timestamp=self._clock(), runtime_state=self._root.runtime_state_machine.state.value,
            broker_status="CONNECTED", market_feed_status=market_feed_status,
            active_positions_count=len(open_groups), last_decision_timestamp=self._last_decision_timestamp,
            last_execution_timestamp=self._last_execution_timestamp,
        )
        if self._observatory is not None:
            self._observatory.record_heartbeat_snapshot(hb)
        return hb

    # ------------------------------------------------------------------ #
    # EOD reconciliation -- stops new entries via F.0's own state
    # transition (ENTRY_ENABLED/POSITION_ACTIVE/MANAGING -> POSTMARKET
    # structurally refuses further process_entry_cycle() calls, per F.1's
    # own _ENTRY_ACCEPTING_STATES guard), performs one final lifecycle
    # evaluation + valuation, and REPORTS unresolved positions -- it
    # never force-closes them, since no existing component owns that
    # decision.
    # ------------------------------------------------------------------ #

    async def run_eod_reconciliation(
        self, latest_prices: Dict[str, float], capital_snapshot: CapitalSafetySnapshot,
        margin_snapshot: Optional[object], margin_explanation: Optional[object], position_groups: list,
        position_health_thresholds: Optional[PositionHealthThresholds],
        portfolio_risk_thresholds: Optional[PortfolioRiskThresholds],
    ) -> EODReconciliationResult:
        self._trading_brain_runtime.run_market_close_sequence()

        valuations = await self._portfolio_engine.revalue_all(latest_prices, self._clock)
        risk_context = await self._portfolio_engine.refresh_risk_context(
            capital_snapshot, None, portfolio_risk_thresholds, margin_snapshot, margin_explanation,
            position_groups, self._clock,
        )
        for position_group_id, valuation in valuations.items():
            reality = await self._registry.get_group_reality(position_group_id)
            await self._lifecycle_runtime.evaluate_group(
                position_group_id, valuation, reality.strategy_family, risk_context.capital_status,
                risk_context.portfolio_status, position_health_thresholds, self._clock,
            )

        unresolved = await self._registry.open_group_ids()
        summary = generate_session_summary(
            self._timeline.entries(), valuations, unresolved, self._root.runtime_state_machine.state.value,
        )
        if self._observatory is not None:
            total_realized = sum(v.total_realized_pnl for v in valuations.values())
            any_unavailable = any(v.total_unrealized_pnl is None for v in valuations.values())
            total_unrealized = None if (any_unavailable or not valuations) else sum(
                v.total_unrealized_pnl for v in valuations.values()
            )
            self._observatory.finalize_session(unresolved, total_realized, total_unrealized)
        return EODReconciliationResult(
            final_state=self._root.runtime_state_machine.state.value, final_valuations=valuations,
            unresolved_position_group_ids=unresolved, summary=summary,
        )
