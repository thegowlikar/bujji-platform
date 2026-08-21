"""Tests — Live Shadow Real-Time Paper Execution sprint: Portfolio
Valuation engine (Part 3) and its dashboard rendering (Part 5)."""
from __future__ import annotations

from datetime import datetime

from bujji.trading_brain.portfolio_valuation.engine import revalue
from bujji.trading_brain.portfolio_valuation.dashboard import render_portfolio_dashboard

FIXED_CLOCK = lambda: datetime(2026, 7, 30, 9, 20, 0)


def _position(symbol="NSE:NIFTY24250CE", side="BUY", qty=150, avg_price=124.15, entry_ts="2026-07-30T09:20:00"):
    return {"symbol": symbol, "side": side, "qty": qty, "avg_price": avg_price, "entry_timestamp": entry_ts}


class TestRevalueBasics:
    def test_no_positions_produces_zero_totals(self):
        v = revalue([], {}, {}, clock=FIXED_CLOCK)
        assert v.legs == ()
        assert v.total_realized_pnl == 0.0
        assert v.total_unrealized_pnl == 0.0
        assert v.total_pnl == 0.0

    def test_long_position_profit_when_price_rises(self):
        v = revalue([_position(side="BUY", avg_price=100.0)], {"NSE:NIFTY24250CE": 110.0}, {}, clock=FIXED_CLOCK)
        assert v.legs[0].unrealized_pnl == (110.0 - 100.0) * 150
        assert v.total_unrealized_pnl == (110.0 - 100.0) * 150

    def test_short_position_profit_when_price_falls(self):
        v = revalue([_position(side="SELL", avg_price=100.0)], {"NSE:NIFTY24250CE": 90.0}, {}, clock=FIXED_CLOCK)
        assert v.legs[0].unrealized_pnl == (100.0 - 90.0) * 150

    def test_short_position_loss_when_price_rises(self):
        v = revalue([_position(side="SELL", avg_price=100.0)], {"NSE:NIFTY24250CE": 110.0}, {}, clock=FIXED_CLOCK)
        assert v.legs[0].unrealized_pnl == (100.0 - 110.0) * 150


class TestMissingPriceHonesty:
    def test_no_observed_price_yields_none_not_zero(self):
        v = revalue([_position()], {}, {}, clock=FIXED_CLOCK)
        assert v.legs[0].current_price is None
        assert v.legs[0].unrealized_pnl is None

    def test_any_missing_price_makes_total_unrealized_none_not_partial(self):
        positions = [_position(symbol="A", avg_price=100.0), _position(symbol="B", avg_price=100.0)]
        v = revalue(positions, {"A": 110.0}, {}, clock=FIXED_CLOCK)  # B has no price.
        assert v.legs[0].unrealized_pnl == (110.0 - 100.0) * 150  # A's own leg IS computed...
        assert v.total_unrealized_pnl is None  # ...but the TOTAL is honestly None, never partially summed.
        assert v.total_pnl is None

    def test_missing_price_never_defaults_to_entry_price(self):
        v = revalue([_position(avg_price=100.0)], {}, {}, clock=FIXED_CLOCK)
        assert v.legs[0].current_price is None  # never silently == entry_price


class TestRealizedPnl:
    def test_realized_pnl_included_even_with_no_open_positions(self):
        v = revalue([], {}, {"NSE:NIFTY24250CE": 500.0}, clock=FIXED_CLOCK)
        assert v.total_realized_pnl == 500.0
        assert v.total_pnl == 500.0  # zero open legs -> unrealized is a real 0.0, not None.

    def test_total_pnl_combines_realized_and_unrealized(self):
        v = revalue([_position(avg_price=100.0)], {"NSE:NIFTY24250CE": 110.0}, {"NSE:NIFTY24250CE": 50.0}, clock=FIXED_CLOCK)
        assert v.total_pnl == 50.0 + (110.0 - 100.0) * 150


class TestStaleness:
    def test_triggering_symbol_leg_is_not_stale(self):
        v = revalue([_position()], {"NSE:NIFTY24250CE": 130.0}, {}, triggering_symbol="NSE:NIFTY24250CE", clock=FIXED_CLOCK)
        assert v.legs[0].price_is_stale is False

    def test_other_leg_with_a_price_is_marked_stale_on_a_different_symbols_tick(self):
        positions = [_position(symbol="A", avg_price=100.0), _position(symbol="B", avg_price=100.0)]
        v = revalue(positions, {"A": 110.0, "B": 105.0}, {}, triggering_symbol="A", clock=FIXED_CLOCK)
        a_leg = next(l for l in v.legs if l.symbol == "A")
        b_leg = next(l for l in v.legs if l.symbol == "B")
        assert a_leg.price_is_stale is False
        assert b_leg.price_is_stale is True

    def test_no_triggering_symbol_means_nothing_is_marked_stale(self):
        v = revalue([_position()], {"NSE:NIFTY24250CE": 130.0}, {}, clock=FIXED_CLOCK)
        assert v.legs[0].price_is_stale is False


class TestDeterminism:
    def test_same_inputs_same_clock_produce_identical_valuation_id(self):
        a = revalue([_position()], {"NSE:NIFTY24250CE": 130.0}, {}, clock=FIXED_CLOCK)
        b = revalue([_position()], {"NSE:NIFTY24250CE": 130.0}, {}, clock=FIXED_CLOCK)
        assert a.valuation_id == b.valuation_id
        assert a == b

    def test_replay_compatibility_no_broker_or_network_import(self):
        """Part 7: the engine must not import any broker/network module --
        confirmed structurally, not just by convention."""
        import ast
        import inspect
        from bujji.trading_brain.portfolio_valuation import engine as eng_module
        tree = ast.parse(inspect.getsource(eng_module))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
        assert not any("broker" in m or "fyers" in m.lower() or "socket" in m for m in imported)


class TestDashboardRendering:
    def test_render_includes_all_requested_fields(self):
        v = revalue([_position()], {"NSE:NIFTY24250CE": 130.0}, {"NSE:NIFTY24250CE": 50.0},
                     triggering_symbol="NSE:NIFTY24250CE", triggering_tick_timestamp="2026-07-30T09:20:05", clock=FIXED_CLOCK)
        text = render_portfolio_dashboard(v)
        for expected in ("entry=", "current=", "unrealized=", "total_realized_pnl", "total_unrealized_pnl", "total_pnl", "as_of"):
            assert expected in text

    def test_render_shows_unknown_never_zero_for_missing_price(self):
        v = revalue([_position()], {}, {}, clock=FIXED_CLOCK)
        text = render_portfolio_dashboard(v)
        assert "UNKNOWN" in text
        assert "0.00" not in text.split("current=")[1].split()[0]
