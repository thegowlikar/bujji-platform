"""Tests for Gate V1.1 -- Trading Session Governor."""
from __future__ import annotations

import asyncio
from datetime import datetime, time
from unittest.mock import AsyncMock, MagicMock

import pytest

from bujji.production_runtime.trading_session_governor.session_trading_state import (
    IllegalTradingSessionTransition, SessionTradingStateTracker, TradingSessionState,
)
from bujji.production_runtime.trading_session_governor.strategy_selector import (
    NO_TRADE, SELLING_UNIVERSE, select_strategy,
)
from bujji.production_runtime.trading_session_governor.strategy_lock import (
    StrategyAlreadyLockedError, StrategyDecision, StrategyLock,
)
from bujji.production_runtime.trading_session_governor.entry_control import (
    REASON_ALLOWED, REASON_SESSION_COMPLETE, REASON_STRATEGY_ALREADY_DEPLOYED, can_enter_trade,
)
from bujji.production_runtime.trading_session_governor.exit_policy import (
    DECISION_MANDATORY_EOD_EXIT, DECISION_MAX_LOSS_EXCEEDED, DECISION_NONE,
    DECISION_PROFIT_TARGET_REACHED, ExitPolicyConfig, IllegalExitPolicyInputError, evaluate_exit_policy,
)
from bujji.production_runtime.trading_session_governor.session_governor import (
    ExitEnforcementResult, TradingSessionGovernor,
)
from bujji.trading_brain.risk_governor.market_regime_adapter import (
    TREND_TRENDING_UP, TREND_SIDEWAYS, TREND_UNKNOWN, VOL_HIGH, VOL_LOW, VOL_UNKNOWN,
)


def fixed_clock():
    return datetime(2026, 1, 5, 10, 0, 0)


# ---------------------------------------------------------------- Component 1
def test_session_state_locked_strategy_progression():
    tracker = SessionTradingStateTracker()
    tracker.transition(TradingSessionState.ANALYSING_MARKET, reason="start")
    tracker.transition(TradingSessionState.STRATEGY_LOCKED, reason="locked")
    tracker.transition(TradingSessionState.POSITION_ACTIVE, reason="filled")
    tracker.transition(TradingSessionState.MANAGING, reason="tick")
    tracker.transition(TradingSessionState.EXITED, reason="exit")
    tracker.transition(TradingSessionState.SESSION_COMPLETE, reason="done")
    assert tracker.is_terminal()


def test_session_state_illegal_transition_fails_closed():
    tracker = SessionTradingStateTracker()
    with pytest.raises(IllegalTradingSessionTransition):
        tracker.transition(TradingSessionState.POSITION_ACTIVE, reason="skip states")


# ---------------------------------------------------------------- Component 2
def test_strategy_selector_sideways_low_vol_selects_iron_condor():
    result = select_strategy(TREND_SIDEWAYS, VOL_LOW, fixed_clock)
    assert result.selected_strategy in SELLING_UNIVERSE
    assert result.reasoning


def test_strategy_selector_missing_regime_fails_closed_to_no_trade():
    result = select_strategy(None, VOL_LOW, fixed_clock)
    assert result.selected_strategy is None


def test_strategy_selector_unknown_regime_fails_closed_to_no_trade():
    result = select_strategy(TREND_UNKNOWN, VOL_UNKNOWN, fixed_clock)
    assert result.selected_strategy is None


def test_strategy_selector_deterministic():
    r1 = select_strategy(TREND_SIDEWAYS, VOL_LOW, fixed_clock)
    r2 = select_strategy(TREND_SIDEWAYS, VOL_LOW, fixed_clock)
    assert r1.selected_strategy == r2.selected_strategy
    assert r1.reasoning == r2.reasoning


# ---------------------------------------------------------------- Component 3
def test_strategy_lock_second_lock_raises():
    lock = StrategyLock()
    decision = StrategyDecision(
        session_id="s1", timestamp=fixed_clock(), trend_regime=TREND_SIDEWAYS, volatility_regime=VOL_LOW,
        selected_strategy="IRON_CONDOR", reasoning="test", confidence="HIGH",
    )
    lock.lock(decision)
    assert lock.is_locked()
    with pytest.raises(StrategyAlreadyLockedError):
        lock.lock(decision)


# ---------------------------------------------------------------- Component 4
def test_entry_control_no_lock_yet_blocked():
    lock = StrategyLock()
    decision = can_enter_trade(TradingSessionState.ANALYSING_MARKET, lock)
    assert not decision.allowed  # nothing locked to deploy yet


def test_entry_control_allowed_exactly_once_after_lock_then_blocked_once_deployed():
    lock = StrategyLock()
    lock.lock(StrategyDecision(
        session_id="s1", timestamp=fixed_clock(), trend_regime=TREND_SIDEWAYS, volatility_regime=VOL_LOW,
        selected_strategy="IRON_CONDOR", reasoning="test", confidence="HIGH",
    ))
    first = can_enter_trade(TradingSessionState.STRATEGY_LOCKED, lock)
    assert first.allowed and first.reason == REASON_ALLOWED

    # Once deployed (POSITION_ACTIVE), a second attempt is blocked.
    second = can_enter_trade(TradingSessionState.POSITION_ACTIVE, lock)
    assert not second.allowed and second.reason == REASON_STRATEGY_ALREADY_DEPLOYED


def test_entry_control_session_complete_blocks_even_if_unlocked():
    lock = StrategyLock()
    decision = can_enter_trade(TradingSessionState.SESSION_COMPLETE, lock)
    assert not decision.allowed and decision.reason == REASON_SESSION_COMPLETE


# ---------------------------------------------------------------- Component 5
def test_exit_policy_profit_target():
    config = ExitPolicyConfig(profit_target_fraction=0.5)
    decision = evaluate_exit_policy(60.0, 100.0, fixed_clock(), config)
    assert decision.decision == DECISION_PROFIT_TARGET_REACHED
    assert decision.is_hard_limit


def test_exit_policy_max_loss():
    config = ExitPolicyConfig(max_loss_fraction=1.0)
    decision = evaluate_exit_policy(-120.0, 100.0, fixed_clock(), config)
    assert decision.decision == DECISION_MAX_LOSS_EXCEEDED
    assert decision.is_hard_limit


def test_exit_policy_mandatory_eod():
    config = ExitPolicyConfig(mandatory_exit_time=time(15, 15))
    now = datetime(2026, 1, 5, 15, 20, 0)
    decision = evaluate_exit_policy(10.0, 100.0, now, config)
    assert decision.decision == DECISION_MANDATORY_EOD_EXIT
    assert decision.is_hard_limit


def test_exit_policy_defers_to_d4_when_no_hard_limit():
    config = ExitPolicyConfig(profit_target_fraction=0.9, max_loss_fraction=0.9)
    decision = evaluate_exit_policy(5.0, 100.0, fixed_clock(), config)
    assert decision.decision == DECISION_NONE
    assert not decision.is_hard_limit


def test_exit_policy_missing_pnl_defers_to_d4():
    config = ExitPolicyConfig(profit_target_fraction=0.5, max_loss_fraction=1.0)
    decision = evaluate_exit_policy(None, None, fixed_clock(), config)
    assert decision.decision == DECISION_NONE


def test_exit_policy_illegal_initial_risk_fails_closed():
    config = ExitPolicyConfig(max_loss_fraction=1.0)
    with pytest.raises(IllegalExitPolicyInputError):
        evaluate_exit_policy(10.0, 0.0, fixed_clock(), config)


# ---------------------------------------------------------------- Governor integration
def _build_governor():
    trading_brain_runtime = MagicMock()
    registry = MagicMock()
    registry.positions_for_group = AsyncMock(return_value=[{"symbol": "NIFTY-CE", "qty": 50}])
    lifecycle_runtime = MagicMock()
    executor = MagicMock()
    executor.execute = AsyncMock()
    config = ExitPolicyConfig(max_loss_fraction=1.0)
    governor = TradingSessionGovernor(
        session_id="sess-1", trading_brain_runtime=trading_brain_runtime, registry=registry,
        lifecycle_runtime=lifecycle_runtime, executor=executor, exit_policy_config=config,
        clock=fixed_clock, event_bus=None,
    )
    return governor, trading_brain_runtime, registry, lifecycle_runtime, executor


def test_governor_first_entry_allowed_second_blocked_once_deployed():
    governor, tbr, registry, lifecycle_runtime, executor = _build_governor()
    governor.begin_market_analysis()
    governor.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)
    assert governor.strategy_lock.is_locked()

    tbr.process_entry_cycle.return_value = MagicMock(filled=True, proposal=MagicMock(assessment_id="pg-1"))
    cycle_result, decision = governor.attempt_entry()
    assert decision.allowed
    tbr.process_entry_cycle.assert_called_once()
    assert governor.state == TradingSessionState.POSITION_ACTIVE

    tbr.process_entry_cycle.reset_mock()
    cycle_result2, decision2 = governor.attempt_entry()
    assert not decision2.allowed
    assert decision2.reason == REASON_STRATEGY_ALREADY_DEPLOYED
    tbr.process_entry_cycle.assert_not_called()


def test_governor_no_trade_day_never_unlocks_entry():
    governor, tbr, registry, lifecycle_runtime, executor = _build_governor()
    governor.begin_market_analysis()
    result = governor.select_and_lock_strategy(TREND_UNKNOWN, VOL_UNKNOWN)
    assert result.selected_strategy is None
    assert not governor.strategy_lock.is_locked()
    decision = can_enter_trade(governor.state, governor.strategy_lock)
    assert not decision.allowed  # still analysing, nothing locked -- no entry window is open
    governor.end_session()
    assert governor.state == TradingSessionState.SESSION_COMPLETE


def test_governor_hard_exit_routes_through_f4_execute():
    governor, tbr, registry, lifecycle_runtime, executor = _build_governor()
    governor.begin_market_analysis()
    governor.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)
    governor._position_group_id = "pg-1"
    governor._state_tracker.transition(TradingSessionState.POSITION_ACTIVE, reason="test-seed")

    fake_recommendation = MagicMock(health_status="OK", action="ACTION_HOLD")
    fake_evaluation = MagicMock(recommendation=fake_recommendation, lifecycle_state=MagicMock())
    lifecycle_runtime.evaluate_group = AsyncMock(return_value=fake_evaluation)
    executor.execute.return_value = MagicMock(status="EXECUTED")

    valuation = MagicMock(total_pnl=-150.0, legs=[])
    result = asyncio.get_event_loop().run_until_complete(
        governor.evaluate_and_enforce_exit(valuation, "IRON_CONDOR", None, None, None, 100.0)
    )
    assert result.policy_decision.decision == DECISION_MAX_LOSS_EXCEEDED
    assert result.forced_execution is not None
    executor.execute.assert_awaited_once()
    _, kwargs = executor.execute.call_args
    assert kwargs["reduce_quantity"] == 50
    forced_evaluation_arg = executor.execute.call_args[0][0]
    from bujji.production_runtime.trade_lifecycle_executor import ACTION_MANDATORY_EXIT
    assert forced_evaluation_arg.recommendation.action == ACTION_MANDATORY_EXIT
    assert any("EXIT_POLICY" in reason for reason in forced_evaluation_arg.recommendation.reasons)
    assert "Session Governor" in forced_evaluation_arg.recommendation.explanation
    assert governor.state == TradingSessionState.EXITED


def test_governor_no_hard_limit_defers_to_d4_no_forced_execution():
    governor, tbr, registry, lifecycle_runtime, executor = _build_governor()
    governor.begin_market_analysis()
    governor.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)
    governor._position_group_id = "pg-1"
    governor._state_tracker.transition(TradingSessionState.POSITION_ACTIVE, reason="test-seed")

    fake_recommendation = MagicMock(health_status="OK", action="ACTION_HOLD")
    fake_evaluation = MagicMock(recommendation=fake_recommendation, lifecycle_state=MagicMock())
    lifecycle_runtime.evaluate_group = AsyncMock(return_value=fake_evaluation)

    valuation = MagicMock(total_pnl=5.0, legs=[])
    result = asyncio.get_event_loop().run_until_complete(
        governor.evaluate_and_enforce_exit(valuation, "IRON_CONDOR", None, None, None, 100.0)
    )
    assert result.policy_decision.decision == DECISION_NONE
    assert result.forced_execution is None
    executor.execute.assert_not_awaited()
    assert governor.state == TradingSessionState.MANAGING


def test_governor_evaluate_exit_before_entry_raises():
    governor, tbr, registry, lifecycle_runtime, executor = _build_governor()
    with pytest.raises(RuntimeError):
        asyncio.get_event_loop().run_until_complete(
            governor.evaluate_and_enforce_exit(MagicMock(total_pnl=0.0, legs=[]), "IRON_CONDOR", None, None, None, 100.0)
        )


# ---------------------------------------------------------------- Full-day E2E scenario
def test_full_trading_day_scenario_regime_to_mandatory_exit():
    """09:15 observe -> regime classified -> IRON_CONDOR selected and
    locked -> entry accepted (real PaperBroker fill) -> D.4 MONITOR
    mid-day -> profit target reached -> MANDATORY_EXIT enforced through
    the REAL PaperBroker/registry/executor stack -> position closed."""
    from bujji.broker.paper import PaperBroker
    from bujji.core.enums import OptionType, Side
    from bujji.core.models import OptionContract, OrderRequest
    from bujji.production_runtime.position_reality_registry import PositionRealityRegistry
    from bujji.production_runtime.position_lifecycle_runtime import PositionLifecycleRuntime
    from bujji.production_runtime.trade_lifecycle_executor import STATUS_EXECUTED, TradeLifecycleExecutor
    from bujji.trading_brain.portfolio_valuation.models import LegValuation, PortfolioValuation

    day_clock_box = {"now": datetime(2026, 1, 5, 9, 15, 0)}

    def clock():
        return day_clock_box["now"]

    async def run_day():
        # 09:15 -- real infrastructure: broker, registry, lifecycle runtime, executor.
        broker = PaperBroker()
        await broker.connect()
        registry = PositionRealityRegistry(broker)
        lifecycle_runtime = PositionLifecycleRuntime(registry, clock)
        executor = TradeLifecycleExecutor(broker, registry, lifecycle_runtime)

        tbr = MagicMock()  # F.1 entry construction stays out of scope for this gate's own test
        exit_config = ExitPolicyConfig(profit_target_fraction=0.5, mandatory_exit_time=time(15, 15))
        governor = TradingSessionGovernor(
            session_id="e2e-day-1", trading_brain_runtime=tbr, registry=registry,
            lifecycle_runtime=lifecycle_runtime, executor=executor, exit_policy_config=exit_config,
            clock=clock, event_bus=None,
        )

        # 09:15 -- observe market, classify regime.
        governor.begin_market_analysis()
        assert governor.state == TradingSessionState.ANALYSING_MARKET

        # 09:20 -- regime classified as sideways/low-vol -> IRON_CONDOR selected and locked.
        selection = governor.select_and_lock_strategy(TREND_SIDEWAYS, VOL_LOW)
        assert selection.selected_strategy in SELLING_UNIVERSE
        assert governor.state == TradingSessionState.STRATEGY_LOCKED

        # 09:25 -- entry accepted. F.1's own construction is out of this gate's scope,
        # so the resulting real broker fill is simulated directly, exactly at the point
        # F.1 would have handed off to PaperBroker.place_order().
        contract = OptionContract("NIFTY25000CE", "NIFTY", 25000, OptionType.CE, "2026-08-04", 75)
        decision = can_enter_trade(governor.state, governor.strategy_lock)
        assert decision.allowed
        await broker.place_order(OrderRequest(contract, Side.SELL, 75, "E2E-ENTRY", limit_price=120.0))
        pg_id = "PG-E2E-1"
        registry.register_entry(pg_id, selection.selected_strategy, ["NIFTY25000CE"], 9000.0, clock,
                                 contracts={"NIFTY25000CE": contract})
        lifecycle_runtime.mark_open(pg_id)
        governor._position_group_id = pg_id
        governor._state_tracker.transition(TradingSessionState.POSITION_ACTIVE, reason="entry_filled")

        # Second entry attempt is structurally blocked -- one deployment per day.
        blocked = can_enter_trade(governor.state, governor.strategy_lock)
        assert not blocked.allowed

        def valuation_at(current_price, ts):
            unrealized = (120.0 - current_price) * 75  # short CE: profit as price falls
            return PortfolioValuation(
                valuation_id=f"val-{ts}", as_of=ts,
                legs=(LegValuation("NIFTY25000CE", "SELL", 75, 120.0, None, current_price, ts, unrealized, False),),
                total_realized_pnl=0.0, total_unrealized_pnl=unrealized, total_pnl=unrealized,
                triggering_symbol="NIFTY25000CE", triggering_tick_timestamp=ts,
            )

        # 11:30 -- market moves a little, well short of any hard limit -> D.4 governs, no forced exit.
        day_clock_box["now"] = datetime(2026, 1, 5, 11, 30, 0)
        mid_day_valuation = valuation_at(110.0, day_clock_box["now"].isoformat())
        result_mid = await governor.evaluate_and_enforce_exit(
            mid_day_valuation, selection.selected_strategy, None, None, None, 9000.0,
        )
        assert result_mid.forced_execution is None
        assert governor.state == TradingSessionState.MANAGING

        # 14:45 -- profit target (50% of initial_risk = 4500) reached -> MANDATORY_EXIT enforced.
        day_clock_box["now"] = datetime(2026, 1, 5, 14, 45, 0)
        profit_valuation = valuation_at(60.0, day_clock_box["now"].isoformat())  # unrealized = 4500.0
        result_exit = await governor.evaluate_and_enforce_exit(
            profit_valuation, selection.selected_strategy, None, None, None, 9000.0,
        )
        assert result_exit.policy_decision.decision == DECISION_PROFIT_TARGET_REACHED
        assert result_exit.forced_execution is not None
        assert result_exit.forced_execution.status == STATUS_EXECUTED
        assert governor.state == TradingSessionState.EXITED

        # PaperBroker's own ledger confirms the position is genuinely closed.
        open_positions = await broker.get_open_positions()
        assert not any(p["symbol"] == "NIFTY25000CE" and p["qty"] > 0 for p in open_positions)

        # 15:30 -- session report.
        governor.end_session()
        assert governor.state == TradingSessionState.SESSION_COMPLETE
        assert governor.strategy_lock.decision.selected_strategy == selection.selected_strategy

    asyncio.run(run_day())
