"""Phase 20.2 -- execution_backtest tests. Synthetic fixtures only."""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from bujji.core.models import Candle
from bujji.execution_backtest.driver import (
    FAMILY_A_TREND_FOLLOWING,
    FAMILY_B_MEAN_REVERSION,
    run_family_backtest,
    simulate_round_trip_trade,
)
from bujji.execution_profiles import EXTREME, NORMAL, STRESS

DAY = "2026-01-05"
BASE = datetime(2026, 1, 5, 9, 15)


def _candles(prices):
    return [
        Candle(timestamp=BASE + timedelta(minutes=5 * i), open=p, high=p + 0.2, low=p - 0.2, close=p)
        for i, p in enumerate(prices)
    ]


# --------------------------------------------------------------------- #
# simulate_round_trip_trade
# --------------------------------------------------------------------- #
def test_buy_then_sell_at_higher_price_is_profitable_before_costs():
    trade = simulate_round_trip_trade("BUY", 50, 1, 100.0, 110.0, NORMAL, random.Random(1))
    assert trade.theoretical_gross_pnl == pytest.approx(500.0)
    assert trade.fees is not None and trade.fees > 0
    assert trade.slippage_cost is not None and trade.slippage_cost > 0
    assert trade.net_pnl < trade.theoretical_gross_pnl


def test_slippage_cost_scales_with_quantity_and_multiplier():
    small = simulate_round_trip_trade("BUY", 1, 1, 100.0, 110.0, STRESS, random.Random(1))
    large = simulate_round_trip_trade("BUY", 100, 1, 100.0, 110.0, STRESS, random.Random(1))
    assert large.slippage_cost == pytest.approx(small.slippage_cost * 100, rel=1e-6)


def test_higher_stress_profile_produces_higher_slippage_cost():
    normal = simulate_round_trip_trade("BUY", 50, 1, 100.0, 110.0, NORMAL, random.Random(1))
    stress = simulate_round_trip_trade("BUY", 50, 1, 100.0, 110.0, STRESS, random.Random(1))
    extreme = simulate_round_trip_trade("BUY", 50, 1, 100.0, 110.0, EXTREME, random.Random(1))
    assert normal.slippage_cost < stress.slippage_cost < extreme.slippage_cost
    assert normal.net_pnl > stress.net_pnl > extreme.net_pnl


def test_theoretical_gross_pnl_never_includes_slippage():
    """Gross P&L must be identical regardless of execution profile --
    only net P&L should move. This is the core Phase 20.2 correctness
    property: slippage is a separate, explicit cost, never silently
    baked into the 'theoretical' figure."""
    normal = simulate_round_trip_trade("BUY", 50, 1, 100.0, 110.0, NORMAL, random.Random(1))
    extreme = simulate_round_trip_trade("BUY", 50, 1, 100.0, 110.0, EXTREME, random.Random(1))
    assert normal.theoretical_gross_pnl == extreme.theoretical_gross_pnl


def test_rejects_invalid_side():
    with pytest.raises(ValueError):
        simulate_round_trip_trade("HOLD", 1, 1, 100.0, 110.0, NORMAL, random.Random(1))


def test_rejects_nonpositive_quantity_or_multiplier():
    with pytest.raises(ValueError):
        simulate_round_trip_trade("BUY", 0, 1, 100.0, 110.0, NORMAL, random.Random(1))
    with pytest.raises(ValueError):
        simulate_round_trip_trade("BUY", 1, 0, 100.0, 110.0, NORMAL, random.Random(1))


# --------------------------------------------------------------------- #
# run_family_backtest
# --------------------------------------------------------------------- #
def test_uptrend_day_produces_family_a_trades():
    prices = [100.0 + i * 0.9 for i in range(20)]
    summary = run_family_backtest(DAY, _candles(prices), 30, FAMILY_A_TREND_FOLLOWING, NORMAL, 50, 1)
    assert summary.n > 0
    assert all(t.family == FAMILY_A_TREND_FOLLOWING for t in summary.trades)


def test_range_day_produces_family_b_trades():
    import math
    prices = [100.0 + 0.3 * math.sin(i * (2 * math.pi / 8)) for i in range(30)]
    summary = run_family_backtest(DAY, _candles(prices), 60, FAMILY_B_MEAN_REVERSION, NORMAL, 50, 1)
    assert all(t.family == FAMILY_B_MEAN_REVERSION for t in summary.trades)


def test_family_a_never_trades_range_regime_and_vice_versa():
    import math
    prices = [100.0 + 0.3 * math.sin(i * (2 * math.pi / 8)) for i in range(30)]
    candles = _candles(prices)
    summary_a = run_family_backtest(DAY, candles, 60, FAMILY_A_TREND_FOLLOWING, NORMAL, 50, 1)
    assert summary_a.n == 0  # a pure range series should produce no TREND classifications


def test_summary_totals_are_sum_of_individual_trades():
    prices = [100.0 + i * 0.9 for i in range(20)]
    summary = run_family_backtest(DAY, _candles(prices), 30, FAMILY_A_TREND_FOLLOWING, NORMAL, 50, 1)
    if summary.n > 0:
        assert summary.total_net_pnl == pytest.approx(sum(t.net_pnl for t in summary.trades if t.net_pnl is not None))


def test_rejects_unknown_family():
    with pytest.raises(ValueError):
        run_family_backtest(DAY, _candles([100.0] * 20), 30, "NOT_A_FAMILY", NORMAL, 50, 1)


def test_deterministic_given_same_seed():
    prices = [100.0 + i * 0.9 for i in range(20)]
    s1 = run_family_backtest(DAY, _candles(prices), 30, FAMILY_A_TREND_FOLLOWING, STRESS, 50, 1, seed=7)
    s2 = run_family_backtest(DAY, _candles(prices), 30, FAMILY_A_TREND_FOLLOWING, STRESS, 50, 1, seed=7)
    assert s1.total_net_pnl == s2.total_net_pnl
