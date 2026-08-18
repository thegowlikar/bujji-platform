#!/usr/bin/env python
"""Phase 20.2 -- real-data proof run. Read-only. Answers: "does
execution reality materially change theoretical results?" for Family
A (trend-following, MIC:TREND) and Family B (mean reversion,
MIC:RANGE), NIFTY futures, last ~60 real trading days, 30-minute
windows, across NORMAL/STRESS/EXTREME profiles. NOT a profitability
claim -- illustrative signal, Level C execution, disclosed as such.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, "/opt/bujji/app")


def _main():
    from bujji.core.models import Candle as CoreCandle
    from bujji.historical_reality.store import HistoricalObservationStore
    from bujji.execution_backtest.driver import (
        FAMILY_A_TREND_FOLLOWING, FAMILY_B_MEAN_REVERSION, run_family_backtest,
    )
    from bujji.execution_profiles import NORMAL, STRESS, EXTREME

    store = HistoricalObservationStore("data/historical_reality/normalized/historical_observations.db")
    start, end = "2026-05-01T00:00:00+05:30", "2026-08-13T23:59:59+05:30"
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
        if len(candles) >= 6:
            days[date_str] = candles
    print(f"{len(days)} real trading days usable\n")

    lot_size = 50  # NIFTY futures lot size -- illustrative, matches this codebase's own MarginLegRequest examples elsewhere; not independently re-verified here.
    quantity = 1

    for family in (FAMILY_A_TREND_FOLLOWING, FAMILY_B_MEAN_REVERSION):
        print(f"=== {family} (30-min windows) ===")
        for profile in (NORMAL, STRESS, EXTREME):
            n_total = n_rejected = 0
            theo_total = net_total = fees_total = slip_total = 0.0
            for date_str, candles in days.items():
                summary = run_family_backtest(date_str, candles, 30, family, profile, quantity, lot_size, seed=42)
                n_total += summary.n
                n_rejected += summary.n_rejected
                if summary.total_theoretical_gross_pnl is not None:
                    theo_total += summary.total_theoretical_gross_pnl
                    net_total += summary.total_net_pnl
                    fees_total += summary.total_fees
                    slip_total += summary.total_slippage_cost
            print(f"  {profile.name:8s}: trades={n_total:5d} rejected={n_rejected:4d}  "
                  f"theoretical_gross=Rs{theo_total:>14,.0f}  net=Rs{net_total:>14,.0f}  "
                  f"fees=Rs{fees_total:>12,.0f}  slippage=Rs{slip_total:>12,.0f}  "
                  f"delta={'%.1f%%' % (100*(net_total-theo_total)/abs(theo_total)) if theo_total else 'n/a'}")
        print()

    store.close()


if __name__ == "__main__":
    _main()
