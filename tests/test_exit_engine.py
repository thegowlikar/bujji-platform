"""Tests — Exit Engine v1 sprint: Parts 1-3 (ExitEngine, ExitDecision,
exit rules) and Part 4 (closing order construction)."""
from __future__ import annotations

from datetime import datetime

from bujji.trading_brain.exit_engine.config import ExitRuleConfig
from bujji.trading_brain.exit_engine.engine import evaluate
from bujji.trading_brain.exit_engine.order_builder import build_closing_orders
from bujji.trading_brain.exit_engine import taxonomy
from bujji.trading_brain.portfolio_valuation.engine import revalue
from bujji.core.enums import Side

FIXED_CLOCK = lambda: datetime(2026, 7, 31, 10, 0, 0)


def _position(symbol="NSE:NIFTY24250CE", side="BUY", avg_price=100.0):
    return {"symbol": symbol, "side": side, "qty": 150, "avg_price": avg_price, "entry_timestamp": "2026-07-31T09:20:00"}


def _valuation(positions, prices, realized=None):
    return revalue(positions, prices, realized or {}, clock=FIXED_CLOCK)


class TestNoOpenPositions:
    def test_no_positions_never_exits(self):
        v = _valuation([], {})
        d = evaluate(v, [], ExitRuleConfig(), clock=FIXED_CLOCK)
        assert d.should_exit is False
        assert d.reason == taxonomy.EXIT_REASON_NO_EXIT
        assert d.affected_positions == ()


class TestMaximumLoss:
    def test_triggers_when_total_pnl_at_or_below_threshold(self):
        positions = [_position(avg_price=100.0)]
        v = _valuation(positions, {"NSE:NIFTY24250CE": 60.0})  # unrealized = -6000
        d = evaluate(v, positions, ExitRuleConfig(max_loss=-5000.0, hard_time_exit=None), clock=FIXED_CLOCK)
        assert d.should_exit is True
        assert d.reason == taxonomy.EXIT_REASON_MAXIMUM_LOSS
        assert d.affected_positions == ("NSE:NIFTY24250CE",)

    def test_does_not_trigger_above_threshold(self):
        positions = [_position(avg_price=100.0)]
        v = _valuation(positions, {"NSE:NIFTY24250CE": 95.0})  # unrealized = -750
        d = evaluate(v, positions, ExitRuleConfig(max_loss=-5000.0, hard_time_exit=None), clock=FIXED_CLOCK)
        assert d.should_exit is False

    def test_disabled_when_none(self):
        positions = [_position(avg_price=100.0)]
        v = _valuation(positions, {"NSE:NIFTY24250CE": 1.0})  # huge loss
        d = evaluate(v, positions, ExitRuleConfig(max_loss=None, hard_time_exit=None), clock=FIXED_CLOCK)
        assert d.should_exit is False

    def test_never_triggers_when_total_pnl_unknown(self):
        """A missing observed price must never be treated as a loss."""
        positions = [_position(avg_price=100.0)]
        v = _valuation(positions, {})  # no price observed -> total_pnl is None
        d = evaluate(v, positions, ExitRuleConfig(max_loss=-1.0, hard_time_exit=None), clock=FIXED_CLOCK)
        assert d.should_exit is False
        assert d.confidence == taxonomy.CONFIDENCE_LOW


class TestProfitTarget:
    def test_triggers_when_total_pnl_at_or_above_threshold(self):
        positions = [_position(avg_price=100.0)]
        v = _valuation(positions, {"NSE:NIFTY24250CE": 140.0})  # unrealized = +6000
        d = evaluate(v, positions, ExitRuleConfig(profit_target=5000.0, hard_time_exit=None), clock=FIXED_CLOCK)
        assert d.should_exit is True
        assert d.reason == taxonomy.EXIT_REASON_PROFIT_TARGET

    def test_does_not_trigger_below_threshold(self):
        positions = [_position(avg_price=100.0)]
        v = _valuation(positions, {"NSE:NIFTY24250CE": 101.0})
        d = evaluate(v, positions, ExitRuleConfig(profit_target=5000.0, hard_time_exit=None), clock=FIXED_CLOCK)
        assert d.should_exit is False


class TestHardTimeExit:
    def test_triggers_at_or_after_configured_time(self):
        positions = [_position(avg_price=100.0)]
        v = _valuation(positions, {"NSE:NIFTY24250CE": 100.0})
        d = evaluate(v, positions, ExitRuleConfig(hard_time_exit="15:15"), now_ist_time="15:15", clock=FIXED_CLOCK)
        assert d.should_exit is True
        assert d.reason == taxonomy.EXIT_REASON_HARD_TIME_EXIT

    def test_triggers_after_configured_time(self):
        positions = [_position(avg_price=100.0)]
        v = _valuation(positions, {"NSE:NIFTY24250CE": 100.0})
        d = evaluate(v, positions, ExitRuleConfig(hard_time_exit="15:15"), now_ist_time="15:20", clock=FIXED_CLOCK)
        assert d.should_exit is True

    def test_does_not_trigger_before_configured_time(self):
        positions = [_position(avg_price=100.0)]
        v = _valuation(positions, {"NSE:NIFTY24250CE": 100.0})
        d = evaluate(v, positions, ExitRuleConfig(hard_time_exit="15:15"), now_ist_time="11:00", clock=FIXED_CLOCK)
        assert d.should_exit is False

    def test_disabled_when_none(self):
        positions = [_position(avg_price=100.0)]
        v = _valuation(positions, {"NSE:NIFTY24250CE": 100.0})
        d = evaluate(v, positions, ExitRuleConfig(hard_time_exit=None), now_ist_time="23:59", clock=FIXED_CLOCK)
        assert d.should_exit is False


class TestStrategyExitPlaceholder:
    def test_placeholder_never_triggers_even_when_enabled(self):
        positions = [_position(avg_price=100.0)]
        v = _valuation(positions, {"NSE:NIFTY24250CE": 100.0})
        d = evaluate(v, positions, ExitRuleConfig(strategy_exit_enabled=True, hard_time_exit=None), clock=FIXED_CLOCK)
        assert d.should_exit is False
        assert d.reason == taxonomy.EXIT_REASON_NO_EXIT


class TestRulePriority:
    def test_maximum_loss_wins_over_hard_time_when_both_satisfied(self):
        positions = [_position(avg_price=100.0)]
        v = _valuation(positions, {"NSE:NIFTY24250CE": 60.0})  # -6000
        d = evaluate(v, positions, ExitRuleConfig(max_loss=-5000.0, hard_time_exit="09:00"), now_ist_time="15:20", clock=FIXED_CLOCK)
        assert d.reason == taxonomy.EXIT_REASON_MAXIMUM_LOSS

    def test_profit_target_wins_over_hard_time_when_both_satisfied(self):
        positions = [_position(avg_price=100.0)]
        v = _valuation(positions, {"NSE:NIFTY24250CE": 140.0})  # +6000
        d = evaluate(v, positions, ExitRuleConfig(profit_target=5000.0, hard_time_exit="09:00"), now_ist_time="15:20", clock=FIXED_CLOCK)
        assert d.reason == taxonomy.EXIT_REASON_PROFIT_TARGET


class TestExitDecisionModel:
    def test_no_broker_imports_in_models(self):
        import ast
        import inspect
        from bujji.trading_brain.exit_engine import models
        tree = ast.parse(inspect.getsource(models))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
        assert not any("broker" in m or "fyers" in m.lower() for m in imported)

    def test_determinism_same_inputs_same_decision_id(self):
        positions = [_position(avg_price=100.0)]
        v = _valuation(positions, {"NSE:NIFTY24250CE": 60.0})
        d1 = evaluate(v, positions, ExitRuleConfig(max_loss=-5000.0, hard_time_exit=None), clock=FIXED_CLOCK)
        d2 = evaluate(v, positions, ExitRuleConfig(max_loss=-5000.0, hard_time_exit=None), clock=FIXED_CLOCK)
        assert d1.decision_id == d2.decision_id
        assert d1 == d2


class TestClosingOrderConstruction:
    def test_builds_one_reversed_order_per_position(self):
        positions = [_position(symbol="NSE:NIFTY24250CE", side="BUY", avg_price=100.0)]
        v = _valuation(positions, {"NSE:NIFTY24250CE": 124.15})
        orders = build_closing_orders(positions, v, clock=FIXED_CLOCK)
        assert len(orders) == 1
        assert orders[0].side == Side.SELL  # reversed from BUY.
        assert orders[0].quantity == 150
        assert orders[0].reference_price == 124.15
        assert orders[0].limit_price is None

    def test_short_position_closes_with_a_buy(self):
        positions = [_position(symbol="NSE:NIFTY24300CE", side="SELL", avg_price=80.0)]
        v = _valuation(positions, {"NSE:NIFTY24300CE": 70.0})
        orders = build_closing_orders(positions, v, clock=FIXED_CLOCK)
        assert orders[0].side == Side.BUY

    def test_empty_positions_produces_no_orders(self):
        v = _valuation([], {})
        assert build_closing_orders([], v, clock=FIXED_CLOCK) == []

    def test_missing_price_falls_back_to_none_not_a_guess(self):
        positions = [_position(symbol="NSE:NIFTY24250CE", avg_price=100.0)]
        v = _valuation(positions, {})  # no observed price.
        orders = build_closing_orders(positions, v, clock=FIXED_CLOCK)
        assert orders[0].reference_price is None

    def test_two_open_legs_produce_two_closing_orders(self):
        positions = [_position(symbol="A", side="BUY", avg_price=100.0), _position(symbol="B", side="SELL", avg_price=80.0)]
        v = _valuation(positions, {"A": 110.0, "B": 70.0})
        orders = build_closing_orders(positions, v, clock=FIXED_CLOCK)
        assert len(orders) == 2
        symbols = {o.contract.symbol for o in orders}
        assert symbols == {"A", "B"}
