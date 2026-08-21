#!/usr/bin/env python
"""Phase 20.1C -- MIC Intraday Validation entrypoint.

Read-only. Pulls real NIFTY futures 5-minute history, generates
rolling 30/60/90/120-minute windows per real trading day, classifies
each via the SAME RegimeBrain used by mic_v0.engine, and produces the
Phase 20.1C intraday validation report. No broker connection, no live
wiring, no order capability.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, "/opt/bujji/app")


def _main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-store-path", required=True)
    parser.add_argument("--instrument", default="NIFTY_FUT_CONTINUOUS")
    parser.add_argument("--start-date", default="2018-02-01T00:00:00+05:30")
    parser.add_argument("--end-date", default="2026-08-13T23:59:59+05:30")
    parser.add_argument("--report-out", default=None)
    args = parser.parse_args()

    from bujji.core.models import Candle as CoreCandle
    from bujji.historical_reality.store import HistoricalObservationStore
    from bujji.mic_v0_validation.intraday_validation import (
        build_intraday_validation_report,
        classify_intraday_window,
        generate_rolling_windows,
    )
    from bujji.mic_v0_validation.models_intraday import WINDOW_LENGTHS_MINUTES

    store = HistoricalObservationStore(args.historical_store_path)

    print(f"Fetching real {args.instrument} FIVE_MINUTE rows {args.start_date} .. {args.end_date} ...")
    rows = store.range(args.instrument, "FIVE_MINUTE", args.start_date, args.end_date)
    print(f"  -> {len(rows)} real rows")

    by_date = defaultdict(list)
    for r in rows:
        by_date[r.observation.identity.timestamp[:10]].append(r)

    print(f"Generating and classifying rolling windows across {len(by_date)} real trading days ...")
    all_readings = []
    days_processed = 0
    for date_str in sorted(by_date):
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
        for window_minutes in WINDOW_LENGTHS_MINUTES:
            windows = generate_rolling_windows(candles, window_minutes)
            for window_candles in windows:
                reading = classify_intraday_window(date_str, window_candles, window_minutes)
                if reading is not None:
                    all_readings.append(reading)

    print(f"  -> {days_processed} real days processed, {len(all_readings)} total window classifications")

    report = build_intraday_validation_report(all_readings, days_processed)
    rendered = report.render()
    print()
    print(rendered)

    if args.report_out:
        with open(args.report_out, "w") as f:
            f.write(rendered)
        print(f"\nReport written to {args.report_out}")

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
