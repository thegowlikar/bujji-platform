"""Trading Session Governor -- BUJJI Options OS v3, Gate V1.1.

The facade composing Components 1-6 on top of the EXISTING, unmodified
F.1 TradingBrainRuntime / F.3 PositionLifecycleRuntime / F.4
TradeLifecycleExecutor / EventBus. Owns SESSION TRADING DISCIPLINE
only -- it calls into each existing owner's own unmodified public
method for everything else (market analysis, risk, execution, MTM,
lifecycle intelligence).

FLOW:
  Market Analysis (caller-supplied regime) -> TradingSessionGovernor
  -> Strategy Construction (F.1, locked strategy only) -> Risk
  Governor (F.1's own D.1-D.6 call, unmodified) -> PaperBroker (F.1's
  own dispatch, unmodified).

HARD EXIT ENFORCEMENT: when the Exit Policy Engine reports a hard
limit (profit target / max loss / mandatory EOD time), this module
constructs an honestly-labeled `RiskActionRecommendation` whose
action is `ACTION_MANDATORY_EXIT` -- a label owned by F.4 itself
(bujji.production_runtime.trade_lifecycle_executor), NOT D.4's own
REDUCE_SIZE. D.4 never produces MANDATORY_EXIT; it exists precisely so
a forced full close is never confused, in the forensic record or by a
future learning system, with an ordinary partial-size reduction. Its
`explanation` names the Session Governor / Exit Policy as the real
source, and it routes through F.4's own `execute()`, which executes
MANDATORY_EXIT via the identical reduce-order codepath as REDUCE_SIZE
(a full close is a reduce to the entire current quantity) -- no new
execution capability was added to F.4, only a new, honestly distinct
label for this one case. D.4's own advisory recommendations (short of
a hard limit) are left to the caller to act on via F.4 directly,
exactly as F.3/F.4 already established -- this module never invents a
reduce/hedge quantity for anything D.4 itself recommends.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Tuple

from bujji.trading_brain.risk_governor.position_lifecycle_intelligence import (
    PositionHealthThresholds, RiskActionRecommendation,
)
from bujji.production_runtime.position_lifecycle_runtime import LifecycleEvaluationResult, PositionLifecycleRuntime
from bujji.production_runtime.position_reality_registry import PositionRealityRegistry
from bujji.production_runtime.trade_lifecycle_executor import (
    ACTION_MANDATORY_EXIT, LifecycleExecutionResult, TradeLifecycleExecutor,
)
from bujji.production_runtime.trading_brain_runtime import TradingBrainCycleResult, TradingBrainRuntime
from bujji.trading_brain.portfolio_valuation.models import PortfolioValuation

from .entry_control import EntryControlDecision, can_enter_trade
from .exit_policy import ExitPolicyConfig, ExitPolicyDecision, evaluate_exit_policy
from .session_trading_state import SessionTradingStateTracker, TradingSessionState
from .strategy_lock import StrategyDecision, StrategyLock
from .strategy_selector import StrategySelectionResult, select_strategy

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class ExitEnforcementResult:
    lifecycle_evaluation: LifecycleEvaluationResult
    policy_decision: ExitPolicyDecision
    forced_execution: Optional[LifecycleExecutionResult]  # non-None only when a hard limit fired


class TradingSessionGovernor:
    def __init__(
        self, session_id: str, trading_brain_runtime: TradingBrainRuntime, registry: PositionRealityRegistry,
        lifecycle_runtime: PositionLifecycleRuntime, executor: TradeLifecycleExecutor,
        exit_policy_config: ExitPolicyConfig, clock: Clock, event_bus=None,
        defined_risk_only: bool = False,
    ) -> None:
        self._session_id = session_id
        self._trading_brain_runtime = trading_brain_runtime
        self._registry = registry
        self._lifecycle_runtime = lifecycle_runtime
        self._executor = executor
        self._exit_policy_config = exit_policy_config
        self._clock = clock
        self._event_bus = event_bus
        # Real money never sells a shape whose loss is unbounded. DERIVED by
        # the caller from the execution mode, never configured on its own --
        # see OptionsOSRunner._startup, which fails closed.
        self._defined_risk_only = defined_risk_only
        self._state_tracker = SessionTradingStateTracker(publish_fn=self._publish_state_change)
        self._strategy_lock = StrategyLock()
        self._position_group_id: Optional[str] = None

    @property
    def state(self) -> TradingSessionState:
        return self._state_tracker.state

    @property
    def strategy_lock(self) -> StrategyLock:
        return self._strategy_lock

    def begin_market_analysis(self) -> None:
        self._state_tracker.transition(TradingSessionState.ANALYSING_MARKET, reason="session_start")

    def select_and_lock_strategy(self, trend_regime: Optional[str], volatility_regime: Optional[str]) -> StrategySelectionResult:
        """Component 2 + 3: select (deterministic lookup, existing
        regime vocabulary only) then lock (one-time, immutable)."""
        result = select_strategy(trend_regime, volatility_regime, self._clock,
                                 defined_risk_only=self._defined_risk_only)
        self._publish("STRATEGY_SELECTION_EVALUATED", {
            "trend_regime": result.trend_regime, "volatility_regime": result.volatility_regime,
            "selected_strategy": result.selected_strategy, "reasoning": result.reasoning, "confidence": result.confidence,
        })
        if result.selected_strategy is not None:
            decision = StrategyDecision(
                session_id=self._session_id, timestamp=self._clock(), trend_regime=result.trend_regime,
                volatility_regime=result.volatility_regime, selected_strategy=result.selected_strategy,
                reasoning=result.reasoning, confidence=result.confidence,
            )
            self._strategy_lock.lock(decision)
            self._state_tracker.transition(TradingSessionState.STRATEGY_LOCKED, reason=f"locked:{result.selected_strategy}")
        return result

    def attempt_entry(self, **entry_kwargs) -> Tuple[Optional[TradingBrainCycleResult], EntryControlDecision]:
        """Component 4. `strategy_family` in entry_kwargs, if any, is
        IGNORED and overwritten with the locked strategy -- the caller
        can never substitute a different strategy than the one already
        locked."""
        decision = can_enter_trade(self._state_tracker.state, self._strategy_lock)
        self._publish("ENTRY_CONTROL_EVALUATED", {"allowed": decision.allowed, "reason": decision.reason})
        if not decision.allowed:
            return None, decision

        entry_kwargs["strategy_family"] = self._strategy_lock.decision.selected_strategy
        cycle_result = self._trading_brain_runtime.process_entry_cycle(**entry_kwargs)
        if cycle_result.filled:
            self._position_group_id = cycle_result.proposal.assessment_id
            self._state_tracker.transition(TradingSessionState.POSITION_ACTIVE, reason="entry_filled")
        return cycle_result, decision

    async def evaluate_and_enforce_exit(
        self, valuation: PortfolioValuation, strategy_type: str, capital_status: Optional[str],
        portfolio_status: Optional[str], position_health_thresholds: Optional[PositionHealthThresholds],
        initial_risk: Optional[float],
    ) -> ExitEnforcementResult:
        """Components 5 + 6 (per-tick portion). D.4's own evaluation is
        called exactly once, unmodified, via F.3's existing
        PositionLifecycleRuntime. The Exit Policy Engine is evaluated
        alongside it. A hard limit is enforced immediately by this
        module, through F.4's own unmodified execute(); D.4's own
        advisory action is left for the caller to act on via F.4
        directly, exactly as already established."""
        if self._position_group_id is None:
            raise RuntimeError("evaluate_and_enforce_exit() called before any position was entered")
        if self._state_tracker.state == TradingSessionState.POSITION_ACTIVE:
            self._state_tracker.transition(TradingSessionState.MANAGING, reason="lifecycle_evaluation")

        evaluation = await self._lifecycle_runtime.evaluate_group(
            self._position_group_id, valuation, strategy_type, capital_status, portfolio_status,
            position_health_thresholds, self._clock,
        )
        policy_decision = evaluate_exit_policy(valuation.total_pnl, initial_risk, self._clock(), self._exit_policy_config)
        self._publish("EXIT_POLICY_EVALUATED", {
            "position_group_id": self._position_group_id, "decision": policy_decision.decision,
            "is_hard_limit": policy_decision.is_hard_limit, "reasoning": policy_decision.reasoning,
        })

        forced_execution = None
        if policy_decision.is_hard_limit:
            positions = await self._registry.positions_for_group(self._position_group_id)
            full_quantity = positions[0]["qty"] if positions else 0
            forced_recommendation = RiskActionRecommendation(
                action=ACTION_MANDATORY_EXIT, health_status=evaluation.recommendation.health_status,
                reasons=(f"EXIT_POLICY:{policy_decision.decision}",),
                explanation=f"Exit Policy (Session Governor) override -- NOT a D.4 recommendation: {policy_decision.reasoning}",
                evaluated_at=self._clock(),
            )
            forced_evaluation = LifecycleEvaluationResult(
                position_group_id=self._position_group_id, recommendation=forced_recommendation,
                lifecycle_state=evaluation.lifecycle_state,
            )
            # Real current market price per leg, straight from F.3's own
            # valuation -- never the entry price. Without this, F.4's own
            # reduce-order builder falls back to the position's entry
            # avg_price and the close realizes ~zero P&L regardless of how
            # far the market actually moved (found during the pre-Monday
            # dry rehearsal).
            reference_prices = {
                leg.symbol: leg.current_price for leg in valuation.legs if leg.current_price is not None
            }
            if full_quantity > 0:
                forced_execution = await self._executor.execute(
                    forced_evaluation, self._clock, reduce_quantity=full_quantity, reference_prices=reference_prices,
                )
                self._state_tracker.transition(TradingSessionState.EXITED, reason=f"exit_policy:{policy_decision.decision}")

        return ExitEnforcementResult(lifecycle_evaluation=evaluation, policy_decision=policy_decision, forced_execution=forced_execution)

    def end_session(self) -> None:
        """Component 6 (session-level portion): terminal bookkeeping
        only -- the caller is responsible for having already run F.5's
        own EOD reconciliation, F.4 execution, etc. This method only
        closes out the trading-discipline state itself. Ending with a
        position still POSITION_ACTIVE/MANAGING is allowed and is
        recorded honestly, never force-closed or silently coerced --
        matching F.5's own "report unresolved positions, never
        force-close" EOD contract."""
        if self._state_tracker.state == TradingSessionState.SESSION_COMPLETE:
            return
        unresolved = self._state_tracker.state in (
            TradingSessionState.POSITION_ACTIVE, TradingSessionState.MANAGING,
        )
        reason = "session_end_with_unresolved_position" if unresolved else "session_end"
        self._state_tracker.transition(TradingSessionState.SESSION_COMPLETE, reason=reason)

    def _publish(self, stage: str, extra: dict) -> None:
        if self._event_bus is None:
            return
        from bujji.core.event_bus import Event, EventType
        payload = {"stage": stage, "session_id": self._session_id}
        payload.update(extra)
        self._event_bus.publish_nowait(Event(type=EventType.DECISION_MADE, payload=payload, timestamp=self._clock()))

    def _publish_state_change(self, previous: TradingSessionState, new: TradingSessionState, reason: str) -> None:
        if self._event_bus is None:
            return
        from bujji.core.event_bus import Event, EventType
        self._event_bus.publish_nowait(Event(
            type=EventType.STATE_CHANGED,
            payload={"stage": f"SESSION_TRADING_{new.value}", "session_id": self._session_id,
                     "from_state": previous.value, "to_state": new.value, "reason": reason},
            timestamp=self._clock(),
        ))
