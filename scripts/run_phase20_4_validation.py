#!/usr/bin/env python
"""Phase 20.4 -- real-data strategy forecast validation. Read-only.
NIFTY futures, 30-minute windows, NORMAL execution profile. All real
history the store has (Phase 20.0: 2018-02-01 .. 2026-08-13). No
threshold changes, no calibration, no synthetic data.
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, "/opt/bujji/app")


def _fmt(x, money=True):
    if x is None:
        return "NA"
    return f"Rs{x:,.0f}" if money else f"{x:.3f}"


def _main():
    from bujji.core.models import Candle as CoreCandle
    from bujji.historical_reality.store import HistoricalObservationStore
    from bujji.execution_profiles import NORMAL
    from bujji.strategy_research import (
        MEAN_REVERSION, TRAIN, VALIDATION, OUT_OF_SAMPLE, TREND_FOLLOWING,
        compute_performance_stats, period_for_date, run_research_day,
    )
    from bujji.strategy_research.eligibility import NO_TRADE

    store = HistoricalObservationStore("data/historical_reality/normalized/historical_observations.db")
    start, end = "2018-02-01T00:00:00+05:30", "2026-08-13T23:59:59+05:30"
    rows = store.range("NIFTY_FUT_CONTINUOUS", "FIVE_MINUTE", start, end)
    print(f"{len(rows)} real rows fetched, {start} .. {end}")

    by_date = defaultdict(list)
    for r in rows:
        by_date[r.observation.identity.timestamp[:10]].append(r)

    days = {}
    for date_str, day_rows in sorted(by_date.items()):
        day_rows = sorted(day_rows, key=lambda r: r.observation.identity.timestamp)
        candles = [
            CoreCandle(
                timestamp=datetime.fromisoformat(r.observation.identity.timestamp),
                open=r.payload["open"], high=r.payload["high"], low=r.payload["low"], close=r.payload["close"],
                volume=r.payload.get("volume") or 0.0,
            )
            for r in day_rows
        ]
        if len(candles) >= 25:  # need >=20 for MA/Bollinger history.
            days[date_str] = candles
    print(f"{len(days)} real trading days usable\n")

    lot_size = 50
    quantity = 1
    window_minutes = 30

    all_results = []
    for date_str, candles in days.items():
        all_results.extend(run_research_day(date_str, candles, window_minutes, NORMAL, quantity, lot_size, seed=42))

    print(f"Total window-N/N+1 pairs evaluated: {len(all_results)}")
    by_family_all = Counter(r.selected_family for r in all_results)
    print("\n=== MIC state -> selected family distribution ===")
    for name, n in by_family_all.most_common():
        print(f"  {name:24s} {n}")

    traded = [r for r in all_results if r.net_pnl is not None]
    print(f"\nTraded (both legs filled): {len(traded)}")

    for family in (TREND_FOLLOWING, MEAN_REVERSION):
        rows_f = [r for r in traded if r.selected_family == family.name]
        if not rows_f:
            print(f"\n=== {family.name}: NO TRADES ===")
            continue
        stats = compute_performance_stats(rows_f)
        n_forecast_correct = sum(1 for r in rows_f if r.forecast_correct)
        n_outcome_worked = sum(1 for r in rows_f if r.outcome_worked)
        n_mic_correct = sum(1 for r in rows_f if r.mic_correct)
        edge_destroyed = sum(1 for r in rows_f if r.forecast_correct and not r.outcome_worked)

        print(f"\n=== {family.name} -- Performance ===")
        print(f"  n={stats.n}  win_rate={_fmt(stats.win_rate, False)}  "
              f"avg_gross={_fmt(stats.avg_gross_return)}  avg_net={_fmt(stats.avg_net_return)}")
        print(f"  profit_factor={_fmt(stats.profit_factor, False)}  max_drawdown={_fmt(stats.max_drawdown)}  "
              f"expectancy={_fmt(stats.expectancy)}")
        print(f"  longest_win_streak={stats.longest_win_streak}  longest_loss_streak={stats.longest_loss_streak}")
        print(f"  MIC persistence accuracy: {n_mic_correct}/{stats.n} ({100*n_mic_correct/stats.n:.1f}%)")
        print(f"  Forecast correct (gross>0, before costs): {n_forecast_correct}/{stats.n} "
              f"({100*n_forecast_correct/stats.n:.1f}%)")
        print(f"  Profitable after costs: {n_outcome_worked}/{stats.n} ({100*n_outcome_worked/stats.n:.1f}%)")
        print(f"  Edge destroyed by execution (forecast right, net wrong): {edge_destroyed}/{stats.n} "
              f"({100*edge_destroyed/stats.n:.1f}%)")

        gross_total = sum(r.theoretical_gross_pnl for r in rows_f)
        net_total = sum(r.net_pnl for r in rows_f)
        print(f"  Before execution costs (gross total): {_fmt(gross_total)}")
        print(f"  After execution costs (net total):    {_fmt(net_total)}")
        print(f"  Difference (execution impact):        {_fmt(gross_total - net_total)}")

        print("\n  --- Regime attribution (mic_state of window N) ---")
        by_regime = defaultdict(list)
        for r in rows_f:
            by_regime[r.mic_state].append(r)
        for regime, rs in sorted(by_regime.items()):
            g = sum(r.theoretical_gross_pnl for r in rs)
            net = sum(r.net_pnl for r in rs)
            print(f"    {regime:20s} n={len(rs):4d}  gross={_fmt(g):>15s}  net={_fmt(net):>15s}")

        print("\n  --- Stability: train (2018-2022) / validation (2023-2024) / out-of-sample (2025-2026) ---")
        by_period = defaultdict(list)
        for r in rows_f:
            by_period[period_for_date(r.date)].append(r)
        for period in (TRAIN, VALIDATION, OUT_OF_SAMPLE):
            rs = by_period.get(period, [])
            if not rs:
                print(f"    {period:14s} n=0")
                continue
            pstats = compute_performance_stats(rs)
            print(f"    {period:14s} n={pstats.n:4d}  win_rate={_fmt(pstats.win_rate, False)}  "
                  f"net_total={_fmt(sum(r.net_pnl for r in rs)):>14s}  expectancy={_fmt(pstats.expectancy)}")

    store.close()


if __name__ == "__main__":
    _main()
