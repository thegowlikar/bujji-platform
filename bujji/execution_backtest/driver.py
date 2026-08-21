"""Phase 20.2 -- minimal backtest driver.

`simulate_round_trip_trade` is the real deliverable: a pure
composition of ALREADY-EXISTING, UNMODIFIED components --
`FillSimulator`/`ChargesCalculator` (Gate F.2, `bujji.broker.
simulation`) and `compute_leg_gross_pnl`/`compute_net_pnl`
(`bujji.position_lifecycle.pnl`, Phase 15K). Nothing here reimplements
fill, charge, or P&L math.

Net P&L convention -- DELIBERATELY DIFFERENT from the existing
`paper_bridge.py` pipeline (Phase 15L), and disclosed here rather than
silently diverging: `paper_bridge.py` computes gross P&L from the
EXIT fill price (already slippage-adjusted by `FillSimulator`) and
then separately subtracts `ExecutionReport.slippage` -- which is a
PER-UNIT price delta, not a position-level currency amount -- via
`compute_net_pnl`. That combination under/over-states the true
slippage cost (units mismatch on the exit leg, and the entry leg's
slippage is not reflected in gross P&L at all since entry uses
`entry_mid`, not a fill price). This module avoids that: gross P&L is
always computed from the strategy's REFERENCE (pre-slippage) entry AND
exit prices, and slippage cost is computed HERE, explicitly, as
`(entry_fill.slippage + exit_fill.slippage) * quantity * multiplier`
-- correctly scaled to currency, on both legs, exactly once. See
docs/PHASE_20_2_EXECUTION_REALITY_REPORT.md for the full disclosure;
existing Phase 15K/15L code is NOT modified by this phase.

`run_family_backtest` is a strategy-signal STUB, not a strategy:
Family A (trend-following) buys/sells in the direction of an
already-classified TREND_UP/TREND_DOWN window and exits at window end;
Family B (mean reversion) fades the window's own net move inside a
classified RANGE window. Neither rule is tuned, optimized, or claimed
profitable -- both exist only to produce real entry/exit REFERENCE
prices so the execution simulator has something concrete to act on.
TRANSITION windows are skipped entirely (no strategy), per the
charter's own "TRANSITION = capital preservation state" rule.
"""
from __future__ import annotations

import random
from typing import List, Optional, Sequence

from bujji.broker.simulation.charges import ChargesCalculator
from bujji.broker.simulation.fill_simulator import FillSimulator, PartialFillConfig
from bujji.broker.simulation.market_snapshot import MarketSnapshot
from bujji.core.models import Candle
from bujji.execution_profiles import ExecutionProfile
from bujji.mic_v0_validation.intraday_validation import classify_intraday_window, generate_rolling_windows
from bujji.mic_v0_validation.models_intraday import INTRADAY_RANGE, INTRADAY_TREND_DOWN, INTRADAY_TREND_UP
from bujji.position_lifecycle.pnl import compute_leg_gross_pnl, compute_net_pnl

from .models import BacktestTradeResult, FamilyBacktestSummary

_OPPOSITE_SIDE = {"BUY": "SELL", "SELL": "BUY"}
FAMILY_A_TREND_FOLLOWING = "FAMILY_A_TREND_FOLLOWING"
FAMILY_B_MEAN_REVERSION = "FAMILY_B_MEAN_REVERSION"


def simulate_round_trip_trade(
    side: str, quantity: int, multiplier: int,
    entry_reference_price: float, exit_reference_price: float,
    execution_profile: ExecutionProfile, rng: random.Random,
    family: str = "", window_minutes: int = 0, window_date: str = "",
) -> BacktestTradeResult:
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
    if quantity <= 0 or multiplier <= 0:
        raise ValueError("quantity and multiplier must be positive")

    exit_side = _OPPOSITE_SIDE[side]
    partial_fill_config = PartialFillConfig()

    entry_fill = FillSimulator.simulate(
        quantity, side, MarketSnapshot(symbol="BACKTEST", last_price=entry_reference_price),
        execution_profile.slippage, execution_profile.latency, execution_profile.rejection,
        partial_fill_config, rng,
    )
    exit_fill = FillSimulator.simulate(
        quantity, exit_side, MarketSnapshot(symbol="BACKTEST", last_price=exit_reference_price),
        execution_profile.slippage, execution_profile.latency, execution_profile.rejection,
        partial_fill_config, rng,
    )

    theoretical_gross_pnl, _status = compute_leg_gross_pnl(
        side, quantity, multiplier, entry_reference_price, exit_reference_price,
    )

    both_filled = entry_fill.fill_price is not None and exit_fill.fill_price is not None
    fees: Optional[float] = None
    slippage_cost: Optional[float] = None
    if both_filled:
        entry_charges = ChargesCalculator.calculate(
            entry_fill.fill_price * entry_fill.fill_quantity, side, execution_profile.charges,
        )
        exit_charges = ChargesCalculator.calculate(
            exit_fill.fill_price * exit_fill.fill_quantity, exit_side, execution_profile.charges,
        )
        fees = entry_charges.total + exit_charges.total
        slippage_cost = (entry_fill.slippage + exit_fill.slippage) * quantity * multiplier

    net_pnl = compute_net_pnl(theoretical_gross_pnl, fees, slippage_cost)

    return BacktestTradeResult(
        side=side, quantity=quantity, multiplier=multiplier,
        entry_reference_price=entry_reference_price, exit_reference_price=exit_reference_price,
        entry_fill_price=entry_fill.fill_price, exit_fill_price=exit_fill.fill_price,
        entry_status=entry_fill.status, exit_status=exit_fill.status,
        theoretical_gross_pnl=theoretical_gross_pnl, fees=fees, slippage_cost=slippage_cost,
        net_pnl=net_pnl, execution_profile_name=execution_profile.name,
        family=family, window_minutes=window_minutes, window_date=window_date,
    )


def _family_signal(family: str, window: Sequence[Candle]):
    """Returns (side, entry_price, exit_price) or None (no trade this window)."""
    entry_price, exit_price = window[0].close, window[-1].close
    if family == FAMILY_A_TREND_FOLLOWING:
        if exit_price > entry_price:
            return "BUY", entry_price, exit_price
        if exit_price < entry_price:
            return "SELL", entry_price, exit_price
        return None
    if family == FAMILY_B_MEAN_REVERSION:
        # Fade the window's own net move -- illustrative only, not tuned.
        if exit_price >= entry_price:
            return "SELL", entry_price, exit_price
        return "BUY", entry_price, exit_price
    raise ValueError(f"unrecognized family {family!r}")


def run_family_backtest(
    date: str, day_candles: Sequence[Candle], window_minutes: int, family: str,
    execution_profile: ExecutionProfile, quantity: int, multiplier: int, seed: int = 42,
) -> FamilyBacktestSummary:
    """One real trading day's worth of rolling windows -> classify each
    via the SAME `classify_intraday_window` Phase 20.1C validated ->
    Family A trades TREND_UP/TREND_DOWN windows, Family B trades RANGE
    windows -- every other window (including TRANSITION) is skipped,
    no strategy. Each traded window becomes exactly one round-trip
    simulated trade."""
    if family not in (FAMILY_A_TREND_FOLLOWING, FAMILY_B_MEAN_REVERSION):
        raise ValueError(f"unrecognized family {family!r}")
    target_regimes = (
        (INTRADAY_TREND_UP, INTRADAY_TREND_DOWN) if family == FAMILY_A_TREND_FOLLOWING else (INTRADAY_RANGE,)
    )
    rng = random.Random(seed)
    trades: List[BacktestTradeResult] = []
    n_rejected = 0

    for window in generate_rolling_windows(day_candles, window_minutes):
        reading = classify_intraday_window(date, window, window_minutes)
        if reading is None or reading.intraday_regime not in target_regimes:
            continue
        signal = _family_signal(family, window)
        if signal is None:
            continue
        side, entry_price, exit_price = signal
        trade = simulate_round_trip_trade(
            side, quantity, multiplier, entry_price, exit_price, execution_profile, rng,
            family=family, window_minutes=window_minutes, window_date=date,
        )
        trades.append(trade)
        if trade.entry_status == "REJECTED" or trade.exit_status == "REJECTED":
            n_rejected += 1

    complete = [t for t in trades if t.net_pnl is not None]
    total_theoretical = sum(t.theoretical_gross_pnl for t in complete) if complete else None
    total_net = sum(t.net_pnl for t in complete) if complete else None
    total_fees = sum(t.fees for t in complete) if complete else None
    total_slippage = sum(t.slippage_cost for t in complete) if complete else None

    return FamilyBacktestSummary(
        family=family, window_minutes=window_minutes, execution_profile_name=execution_profile.name,
        n=len(complete), n_rejected=n_rejected,
        total_theoretical_gross_pnl=total_theoretical, total_net_pnl=total_net,
        total_fees=total_fees, total_slippage_cost=total_slippage, trades=tuple(trades),
    )
