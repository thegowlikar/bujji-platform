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
from bujji.production_runtime import exit_lifecycle as _exit_lifecycle
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
        # M4: the tracker is now a CACHE. The durable authority is the
        # SESSION_TRANSITION stream in the position group journal, and state
        # is derived from it plus broker truth. These are injected rather than
        # constructed so a session that has no journal still runs -- it simply
        # cannot certify itself, which the safety verdict reports.
        self._lifecycle_journal = None
        self._lifecycle_session_id = None
        self._lifecycle_clock = None
        self._lifecycle_logger = None
        self._strategy_lock = StrategyLock()
        self._position_group_id: Optional[str] = None

    @property
    def state(self) -> TradingSessionState:
        return self._state_tracker.state

    @property
    def strategy_lock(self) -> StrategyLock:
        return self._strategy_lock

    def begin_market_analysis(self) -> None:
        self._transition(TradingSessionState.ANALYSING_MARKET, "session_start")


    def bind_lifecycle_journal(self, journal, session_id, clock, logger=None) -> None:
        """Give the governor the durable authority for its own transitions.

        Called once by the runner at startup. Until it is called, transitions
        still move the in-memory cache -- so an unbound governor behaves as it
        always did -- but nothing is journaled and the session cannot be
        reconstructed after a restart. `_transition` records that fact rather
        than failing silently.
        """
        self._lifecycle_journal = journal
        self._lifecycle_session_id = session_id
        self._lifecycle_clock = clock
        self._lifecycle_logger = logger

    def _transition(self, target, reason: str, evidence_ref: str = "") -> None:
        """THE single transition path. Journal first, then move the cache.

        JOURNAL FIRST, DELIBERATELY. If the append fails the cache does not
        move, so the in-memory state can never claim something the durable
        record does not. The opposite order would leave a process believing a
        transition that a restart could not see.
        """
        prior = self._state_tracker.state
        if target == prior:
            return
        journal = self._lifecycle_journal
        if journal is not None:
            from bujji.production_runtime.session_lifecycle import record_transition

            record_transition(
                journal, self._lifecycle_session_id, prior, target,
                cause=reason, evidence_ref=evidence_ref or reason,
                clock=self._lifecycle_clock, logger=self._lifecycle_logger)
        else:
            self._unjournaled_transitions = getattr(
                self, "_unjournaled_transitions", 0) + 1
        self._state_tracker.transition(target, reason=reason)

    def lifecycle_unjournaled_count(self) -> int:
        """Transitions that moved the cache without reaching the journal.
        Non-zero means this session cannot be reconstructed."""
        return getattr(self, "_unjournaled_transitions", 0)

    def select_and_lock_strategy(self, trend_regime: Optional[str], volatility_regime: Optional[str]) -> StrategySelectionResult:
        """Component 2 + 3: select (deterministic lookup, existing
        regime vocabulary only) then lock (one-time, immutable)."""
        result = select_strategy(trend_regime, volatility_regime, self._clock,
                                 defined_risk_only=self._defined_risk_only)
        self._publish("STRATEGY_SELECTION_EVALUATED", {
            "trend_regime": result.trend_regime, "volatility_regime": result.volatility_regime,
            "selected_strategy": result.selected_strategy, "reasoning": result.reasoning, "confidence": result.confidence,
            # EVERY shape considered, with the reason it was or was not
            # available -- not just the winner. Without this the journal can
            # say what Bujji traded but never why it declined the alternatives,
            # which is the half of the record an operator actually needs on a
            # no-trade day.
            "candidates": [
                {"family": c.family, "status": c.status,
                 "reason_code": c.reason_code, "detail": c.detail}
                for c in result.candidates
            ],
        })
        # ALREADY LOCKED IS THE RETRY CASE, NOT AN ERROR.
        #
        # `entry_control`'s own module docstring states the contract this
        # method was breaking: "STRATEGY_ALREADY_DEPLOYED, not 'strategy is
        # locked,' is the condition that blocks a second entry; being locked
        # is a PRECONDITION of the single allowed entry, not a reason to block
        # it." `can_enter_trade` implements exactly that -- it returns ALLOWED
        # for STRATEGY_LOCKED + is_locked, which is only ever reached on a
        # RE-attempt. That branch could never run, because this method called
        # `lock()` again first and `lock()` raises.
        #
        # WHAT IT COST. A continuous session re-attempts entry every 300s for
        # up to 96 cycles (config: session.continuous). Any entry that locked
        # and then failed downstream -- margin veto, liquidity rejection at
        # MIN_OPEN_INTEREST, an unfilled order, a contained partial -- leaves
        # the lock set and the state at STRATEGY_LOCKED. The next stable cycle
        # therefore raised StrategyAlreadyLockedError out of an unguarded call
        # at bujji_options_os_runner.py:2340 and killed the session. When the
        # failure was a PARTIAL ORPHAN, that killed a session holding live
        # naked legs -- the 2026-08-21 shape reached by a second route.
        #
        # `StrategyLock.lock()` is UNCHANGED and still raises: the structural
        # one-strategy-per-day guarantee, and the test that pins it
        # (test_strategy_lock_second_lock_raises), are untouched. The second
        # CALL was the defect, never the guard.
        if self._strategy_lock.is_locked():
            locked = self._strategy_lock.decision
            if (result.selected_strategy is not None
                    and result.selected_strategy != locked.selected_strategy):
                # ONE STRATEGY PER DAY IS UNCHANGED. `attempt_entry` overwrites
                # strategy_family from the lock and always has, so the locked
                # family is what gets placed either way. This records that the
                # regime has since moved -- evidence the operator needs, which
                # the crash previously destroyed. Whether a diverged regime
                # should also REFUSE the retry is a trading decision and is
                # NOT made here.
                self._publish("STRATEGY_SELECTION_DIVERGED", {
                    "locked_strategy": locked.selected_strategy,
                    "locked_at": locked.timestamp.isoformat(),
                    "would_now_select": result.selected_strategy,
                    "trend_regime": result.trend_regime,
                    "volatility_regime": result.volatility_regime,
                })
            # THE LOCKED DECISION IS RETURNED, NEVER THE FRESH ONE. The caller
            # writes `selection.selected_strategy` into the session summary;
            # reporting a family the session is not locked to would make the
            # artifact disagree with what `attempt_entry` actually places.
            return StrategySelectionResult(
                selected_strategy=locked.selected_strategy,
                trend_regime=locked.trend_regime,
                volatility_regime=locked.volatility_regime,
                reasoning=(f"session already locked to {locked.selected_strategy} at "
                           f"{locked.timestamp.isoformat()}; this cycle re-attempts "
                           f"entry with the locked strategy"),
                confidence=locked.confidence,
                evaluated_at=self._clock(),
            )

        if result.selected_strategy is not None:
            decision = StrategyDecision(
                session_id=self._session_id, timestamp=self._clock(), trend_regime=result.trend_regime,
                volatility_regime=result.volatility_regime, selected_strategy=result.selected_strategy,
                reasoning=result.reasoning, confidence=result.confidence,
            )
            self._strategy_lock.lock(decision)
            self._transition(TradingSessionState.STRATEGY_LOCKED, f"locked:{result.selected_strategy}")
        return result

    def mark_position_deployed(self, reason: str) -> None:
        """Declare a live position this governor did not open via `attempt_entry`.

        The only caller is the runner's ORPHAN registration: legs that
        filled while the entry as a whole did not, or legs whose broker
        truth came back UNKNOWN. Both mean a position may exist.

        WHY THIS EXISTS. `attempt_entry` transitions to POSITION_ACTIVE only
        `if cycle_result.filled`, so an orphan left the session in
        STRATEGY_LOCKED -- the one state `can_enter_trade` returns ALLOWED
        for. Nothing blocked a SECOND entry stacked on top of live orphaned
        legs; that was masked only by the StrategyAlreadyLockedError crash
        this commit removes. Moving the state to POSITION_ACTIVE blocks it
        through the documented mechanism (STRATEGY_ALREADY_DEPLOYED) rather
        than a new one.

        FAIL CLOSED ON UNKNOWN. A BROKER_TRUTH_UNKNOWN leg may not exist at
        all, and this still refuses further entry for the session. That
        asymmetry is deliberate and matches `_register_orphaned_legs`' own:
        declining one entry costs a missed trade, while stacking a second
        position on a live naked leg is unbounded.
        """
        self._transition(TradingSessionState.POSITION_ACTIVE, reason)

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
            self._transition(TradingSessionState.POSITION_ACTIVE, "entry_filled")
        return cycle_result, decision

    async def evaluate_and_enforce_exit(
        self, valuation: PortfolioValuation, strategy_type: str, capital_status: Optional[str],
        portfolio_status: Optional[str], position_health_thresholds: Optional[PositionHealthThresholds],
        initial_risk: Optional[float], force_exit_reason: Optional[str] = None,
    ) -> ExitEnforcementResult:
        """Components 5 + 6 (per-tick portion). D.4's own evaluation is
        called exactly once, unmodified, via F.3's existing
        PositionLifecycleRuntime. The Exit Policy Engine is evaluated
        alongside it. A hard limit is enforced immediately by this
        module, through F.4's own unmodified execute(); D.4's own
        advisory action is left for the caller to act on via F.4
        directly, exactly as already established.

        `force_exit_reason` (2026-08-21): drives the SAME hard-limit branch
        below regardless of what the exit policy decided. Added for the
        emergency brake, which previously called
        `TradingBrainRuntime.run_market_close_sequence()` -- four lines that
        transition POSTMARKET then COMPLETE and place NO order. Its in-code
        comment claimed it "reus[ed] the same mandatory close-everything
        sequence _eod_close uses"; that sequence fires nothing. The brake now
        reuses THIS path, which is the only one that actually submits.
        """
        if self._position_group_id is None:
            raise RuntimeError("evaluate_and_enforce_exit() called before any position was entered")
        if self._state_tracker.state == TradingSessionState.POSITION_ACTIVE:
            self._transition(TradingSessionState.MANAGING, "lifecycle_evaluation")

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
        if policy_decision.is_hard_limit or force_exit_reason is not None:
            positions = await self._registry.positions_for_group(self._position_group_id)
            # BROKER RESIDUAL, PER LEG. This read `positions[0]["qty"]` and
            # applied that single figure to EVERY leg. For a strangle whose
            # legs carry equal lots it happens to be right; the moment they
            # differ -- a partial fill, a partial prior reduce, an adjusted
            # leg -- it over-reduces the smaller leg, and over-reducing a
            # short OPENS AN OPPOSITE POSITION. The quantity now comes from
            # each leg's own broker-reported qty.
            quantity_by_symbol = {
                p["symbol"]: int(p["qty"]) for p in positions if int(p.get("qty", 0)) > 0
            }
            full_quantity = max(quantity_by_symbol.values()) if quantity_by_symbol else 0
            _trigger = (f"EMERGENCY_BRAKE:{force_exit_reason}" if force_exit_reason is not None
                        else f"EXIT_POLICY:{policy_decision.decision}")
            forced_recommendation = RiskActionRecommendation(
                action=ACTION_MANDATORY_EXIT, health_status=evaluation.recommendation.health_status,
                reasons=(_trigger,),
                explanation=(f"Emergency brake override -- NOT a D.4 recommendation: {force_exit_reason}"
                             if force_exit_reason is not None
                             else f"Exit Policy (Session Governor) override -- NOT a D.4 recommendation: {policy_decision.reasoning}"),
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
                    forced_evaluation, self._clock,
                    reduce_quantity=full_quantity, reference_prices=reference_prices,
                    quantity_by_symbol=quantity_by_symbol,
                    # WHY this exit happened, carried into the journal. A
                    # forced exit IS the emergency brake; anything else is the
                    # strategy's own decision. The distinction is recorded
                    # rather than inferred later from timestamps.
                    cause=(_exit_lifecycle.CAUSE_EMERGENCY if force_exit_reason
                           else _exit_lifecycle.CAUSE_STRATEGY),
                )
                # EXITED ONLY ON A CONFIRMED EXIT. This transitioned on the
                # mere RETURN of execute(), whether the orders filled, were
                # rejected, or timed out to BROKER_TRUTH_UNKNOWN -- so
                # end_session() then reported the session resolved after a
                # failed flatten. The status is now read.
                from bujji.production_runtime.trade_lifecycle_executor import STATUS_EXECUTED

                if getattr(forced_execution, "status", None) == STATUS_EXECUTED:
                    self._transition(
                        TradingSessionState.EXITED, f"exit_confirmed:{_trigger}")
                else:
                    # Deliberately left in MANAGING: the position is still
                    # live as far as anything can prove, and end_session()'s
                    # own "unresolved" reporting depends on this state.
                    self._publish("EXIT_NOT_CONFIRMED", {
                        "position_group_id": self._position_group_id,
                        "status": getattr(forced_execution, "status", None),
                        "trigger": _trigger,
                    })
            elif force_exit_reason is not None:
                # An emergency brake that found NO broker position to reduce.
                # Recorded rather than silently passing: either the position
                # genuinely closed already, or the registry could not see it.
                self._publish("EMERGENCY_EXIT_NO_POSITION_FOUND", {
                    "position_group_id": self._position_group_id, "trigger": _trigger,
                })

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
        self._transition(TradingSessionState.SESSION_COMPLETE, reason)

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
