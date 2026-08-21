#!/usr/bin/env python
"""Phase 20.3 -- real-data strategy research + attribution run.
Read-only. NIFTY futures, 30-minute windows, NORMAL execution
profile. Answers: does MIC's regime call, tested on the NEXT window
(never the same one), predict which window the selected family should
actually trade -- and when it does, does the family survive real
execution costs?
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, "/opt/bujji/app")


def _main():
    from bujji.core.models import Candle as CoreCandle
    from bujji.historical_reality.store import HistoricalObservationStore
    from bujji.execution_profiles import NORMAL
    from bujji.strategy_research import run_research_day
    from bujji.strategy_research.eligibility import NO_TRADE

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

    lot_size = 50
    quantity = 1
    window_minutes = 30

    all_results = []
    for date_str, candles in days.items():
        all_results.extend(run_research_day(date_str, candles, window_minutes, NORMAL, quantity, lot_size, seed=42))

    print(f"Total window-N/N+1 pairs evaluated: {len(all_results)}")

    by_family = Counter(r.selected_family for r in all_results)
    print("\n=== MIC state -> selected family distribution ===")
    for name, n in by_family.most_common():
        print(f"  {name:20s} {n}")

    traded = [r for r in all_results if r.net_pnl is not None]
    print(f"\nTraded (non-NO_TRADE, both legs filled): {len(traded)}")

    by_fam_traded = defaultdict(list)
    for r in traded:
        by_fam_traded[r.selected_family].append(r)

    print("\n=== Per-family: gross vs net, attribution quadrants ===")
    for fam, rows in by_fam_traded.items():
        theo = sum(r.theoretical_gross_pnl for r in rows)
        net = sum(r.net_pnl for r in rows)
        drag = sum(r.execution_drag for r in rows)
        n_correct = sum(1 for r in rows if r.mic_correct)
        quadrants = Counter(r.attribution for r in rows)
        print(f"\n{fam}  (n={len(rows)})")
        print(f"  theoretical_gross=Rs{theo:,.0f}  net=Rs{net:,.0f}  execution_drag=Rs{drag:,.0f}")
        print(f"  MIC persistence accuracy (regime held N->N+1): {n_correct}/{len(rows)} "
              f"({100*n_correct/len(rows):.1f}%)")
        for q in sorted(quadrants):
            print(f"    {q}: {quadrants[q]}")

    store.close()


if __name__ == "__main__":
    _main()
