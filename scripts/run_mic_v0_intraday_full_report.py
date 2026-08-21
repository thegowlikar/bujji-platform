#!/usr/bin/env python
"""Phase 20.1C -- full intraday MIC validation run + extended report.

Read-only. Reuses classify_intraday_window / generate_rolling_windows /
build_intraday_validation_report UNMODIFIED (no threshold changes, no
MIC redesign) -- this script only adds extended REPORTING (median
stats, percentage distribution, opportunity frequency, a dedicated
TRANSITION section) over the same real classifications.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, "/opt/bujji/app")


def _median(values):
    real = [v for v in values if v is not None]
    return round(statistics.median(real), 4) if real else None


def _main():
    from bujji.core.models import Candle as CoreCandle
    from bujji.historical_reality.store import HistoricalObservationStore
    from bujji.mic_v0_validation.intraday_validation import (
        build_intraday_validation_report,
        classify_intraday_window,
        generate_rolling_windows,
    )
    from bujji.mic_v0_validation.models_intraday import ALL_INTRADAY_REGIMES, WINDOW_LENGTHS_MINUTES

    store = HistoricalObservationStore("data/historical_reality/normalized/historical_observations.db")

    start, end = "2018-02-01T00:00:00+05:30", "2026-08-13T23:59:59+05:30"
    print(f"Fetching real NIFTY_FUT_CONTINUOUS FIVE_MINUTE rows {start} .. {end} ...", flush=True)
    rows = store.range("NIFTY_FUT_CONTINUOUS", "FIVE_MINUTE", start, end)
    print(f"  -> {len(rows)} real rows", flush=True)

    by_date = defaultdict(list)
    for r in rows:
        by_date[r.observation.identity.timestamp[:10]].append(r)

    print(f"Classifying rolling windows across {len(by_date)} real trading days ...", flush=True)
    all_readings = []
    days_processed = 0
    dates_seen = []
    for i, date_str in enumerate(sorted(by_date)):
        day_rows = sorted(by_date[date_str], key=lambda r: r.observation.identity.timestamp)
        candles = [
            CoreCandle(
                timestamp=datetime.fromisoformat(r.observation.identity.timestamp),
                open=r.payload["open"], high=r.payload["high"], low=r.payload["low"], close=r.payload["close"],
                volume=r.payload.get("volume") or 0.0,
            )
            for r in day_rows
        ]
        if len(candles) < 6:
            continue
        days_processed += 1
        dates_seen.append(date_str)
        for window_minutes in WINDOW_LENGTHS_MINUTES:
            for window_candles in generate_rolling_windows(candles, window_minutes):
                reading = classify_intraday_window(date_str, window_candles, window_minutes)
                if reading is not None:
                    all_readings.append(reading)
        if (i + 1) % 250 == 0:
            print(f"  ... {i + 1}/{len(by_date)} days, {len(all_readings)} readings so far", flush=True)

    print(f"DONE: {days_processed} real days, {len(all_readings)} total window classifications\n", flush=True)

    # Months spanned (real calendar span, for opportunity-frequency reporting).
    first_date = datetime.fromisoformat(dates_seen[0])
    last_date = datetime.fromisoformat(dates_seen[-1])
    months_spanned = max(1.0, (last_date - first_date).days / 30.44)

    lines = []
    lines.append("Phase 20.1C -- Extended Intraday MIC Validation Report")
    lines.append("=" * 70)
    lines.append(f"Real trading days: {days_processed}   Span: {dates_seen[0]} to {dates_seen[-1]} "
                  f"(~{round(months_spanned, 1)} months)")
    lines.append(f"Total window classifications: {len(all_readings)}")
    lines.append("")

    for wm in WINDOW_LENGTHS_MINUTES:
        relevant = [r for r in all_readings if r.window_minutes == wm]
        total_at_wm = len(relevant)
        lines.append(f"### Window: {wm} minutes  (total windows: {total_at_wm}) ###")
        lines.append("")
        for label in ALL_INTRADAY_REGIMES:
            group = [r for r in relevant if r.intraday_regime == label]
            n = len(group)
            pct = round(100.0 * n / total_at_wm, 2) if total_at_wm else 0.0
            per_month = round(n / months_spanned, 1)
            lines.append(f"{label}")
            lines.append(f"  samples: {n}  ({pct}% of {wm}-min windows)")
            lines.append(f"  opportunity frequency: ~{per_month} windows/month")
            lines.append(f"  median ER: {_median([r.efficiency_ratio for r in group])}")
            lines.append(f"  median ADX: {_median([r.adx for r in group])}")
            lines.append(f"  median persistence: {_median([r.persistence for r in group])}")
            lines.append(f"  median reversal_frequency: {_median([r.reversal_frequency for r in group])}")
            lines.append(f"  median realized_vol: {_median([r.realized_vol for r in group])}")
            lines.append("")
        lines.append("")

    # Reused, unmodified separation analysis.
    report = build_intraday_validation_report(all_readings, days_processed)
    lines.append(report.render())

    rendered = "\n".join(lines)
    print(rendered)
    with open("/tmp/mic_v0_intraday_full_report.txt", "w") as f:
        f.write(rendered)
    print("\nWritten to /tmp/mic_v0_intraday_full_report.txt")

    store.close()


if __name__ == "__main__":
    _main()
