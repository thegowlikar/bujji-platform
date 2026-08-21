#!/usr/bin/env python
"""Phase 19.20.4 -- EOD Market Data Integrity & Reconciliation entrypoint.

Read-only. Validates the trading day, loads MicrostructureStore /
Phase 19.19 HistoricalObservationStore / bhavcopy (all read-only),
runs every Phase 19.20.4 check, builds ONE DailyIntegrityReport, and
appends it via report_store.py. Never writes to either source store.

No broker connection, no FYERS import, no order-placement capability
anywhere in this file -- it only ever reads already-captured data.

NOT installed as a systemd service by this phase (Phase 19.20.4's own
explicit boundary) -- this script is for manual/foreground invocation
and future systemd wiring only after that is separately approved.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, "/opt/bujji/app")


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="session date, YYYY-MM-DD")
    parser.add_argument("--instrument", default="NSE:NIFTY50-INDEX")
    parser.add_argument("--microstructure-store-path", required=True)
    parser.add_argument("--historical-store-path", required=True)
    parser.add_argument("--report-store-path", required=True)
    parser.add_argument("--bhavcopy-path", default=None,
                         help="optional NSE bhavcopy CSV; omitted means bhavcopy comparison reports ADVISORY")
    parser.add_argument("--session-start", default="09:15:00+05:30")
    parser.add_argument("--session-end", default="15:30:00+05:30")
    args = parser.parse_args()

    log = logging.getLogger("bujji.run_eod_reconciliation")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from bujji.market_calendar import MarketCalendar

    calendar = MarketCalendar()
    warning = calendar.verification_warning()
    if warning:
        print(f"WARNING: {warning}", file=sys.stderr)
    is_trading_day, reason = calendar.is_trading_day(date.fromisoformat(args.date))
    if not is_trading_day:
        print(f"not a trading day, skipping cleanly: {reason}")
        return 0

    from bujji.historical_reality.store import HistoricalObservationStore
    from bujji.market_data_integrity.eod_reconciliation import build_daily_integrity_report
    from bujji.market_data_integrity.report_store import record_integrity_report
    from bujji.market_microstructure.store import MicrostructureStore
    from bujji.market_observation.taxonomy import RESOLUTION_FIVE_MINUTE
    from bujji.state_persistence.store import EventStore

    session_start_iso = f"{args.date}T{args.session_start}"
    session_end_iso = f"{args.date}T{args.session_end}"
    now_iso = datetime.now(timezone.utc).isoformat()

    micro_store = MicrostructureStore(args.microstructure_store_path)
    hist_store = HistoricalObservationStore(args.historical_store_path)

    minute_observations = micro_store.get_by_instrument(args.instrument)
    minute_observations = [o for o in minute_observations if o.session_date == args.date]

    session_log_rows = micro_store.get_capture_sessions(args.date)

    five_minute_rows = hist_store.range(
        args.instrument, RESOLUTION_FIVE_MINUTE, session_start_iso, session_end_iso,
    )

    bhavcopy_row = None
    if args.bhavcopy_path:
        from bujji.market_data_integrity.eod_reconciliation import find_bhavcopy_row, load_bhavcopy_csv
        rows = load_bhavcopy_csv(args.bhavcopy_path)
        bhavcopy_row = find_bhavcopy_row(rows, tckr_symb=args.instrument.split(":")[-1])

    report = build_daily_integrity_report(
        session_date=args.date, instrument=args.instrument, minute_observations=minute_observations,
        session_start_iso=session_start_iso, session_end_iso=session_end_iso, now_iso=now_iso,
        capture_session_log_rows=session_log_rows, five_minute_historical_rows=five_minute_rows,
        bhavcopy_row=bhavcopy_row,
    )

    report_store = EventStore(args.report_store_path)
    record_integrity_report(report_store, report, recorded_at=datetime.now(timezone.utc))

    log.info("integrity report recorded: date=%s status=%s quality_score=%s issues=%d",
              report.session_date, report.overall_status, report.quality_score, len(report.issues))
    print(f"overall_status={report.overall_status} quality_score={report.quality_score} "
          f"issues={len(report.issues)}")

    micro_store.close()
    hist_store.close()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
