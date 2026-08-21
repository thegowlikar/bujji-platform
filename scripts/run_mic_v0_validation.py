#!/usr/bin/env python
"""Phase 20.1B -- MIC Validation entrypoint.

Read-only. Pulls real NIFTY futures 5-minute history and real India
VIX daily history from HistoricalObservationStore, classifies every
real trading day, and produces a MIC Validation Report. No broker
connection, no live wiring, no order capability.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict

sys.path.insert(0, "/opt/bujji/app")


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-store-path", required=True)
    parser.add_argument("--instrument", default="NIFTY_FUT_CONTINUOUS")
    parser.add_argument("--start-date", default="2018-02-01T00:00:00+05:30")
    parser.add_argument("--end-date", default="2026-08-13T23:59:59+05:30")
    parser.add_argument("--report-out", default=None)
    args = parser.parse_args()

    from bujji.historical_reality.store import HistoricalObservationStore
    from bujji.mic_v0_validation.validation import classify_day, build_validation_report

    store = HistoricalObservationStore(args.historical_store_path)

    print(f"Fetching real {args.instrument} FIVE_MINUTE rows {args.start_date} .. {args.end_date} ...")
    futures_rows = store.range(args.instrument, "FIVE_MINUTE", args.start_date, args.end_date)
    print(f"  -> {len(futures_rows)} real rows")

    print("Fetching real India VIX DAILY history ...")
    vix_rows = store.range("NSE:INDIAVIX-INDEX", "DAILY", "2008-01-01T00:00:00+05:30", args.end_date)
    vix_by_date = {r.observation.identity.timestamp[:10]: r.payload["close"] for r in vix_rows}
    sorted_vix_dates = sorted(vix_by_date)
    print(f"  -> {len(vix_by_date)} real VIX daily closes")

    by_date = defaultdict(list)
    for r in futures_rows:
        by_date[r.observation.identity.timestamp[:10]].append(r)

    print(f"Classifying {len(by_date)} real trading days ...")
    classifications = []
    for date_str in sorted(by_date):
        if date_str not in vix_by_date:
            continue  # honest skip -- no fabricated VIX value for a missing date.
        idx = sorted_vix_dates.index(date_str)
        trailing_vix = [vix_by_date[d] for d in sorted_vix_dates[:idx]]
        current_vix = vix_by_date[date_str]
        result = classify_day(date_str, by_date[date_str], current_vix, trailing_vix)
        if result is not None:
            classifications.append(result)

    print(f"  -> {len(classifications)} days successfully classified (real data only)")

    report = build_validation_report(classifications)
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
