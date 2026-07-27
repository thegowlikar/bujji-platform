#!/usr/bin/env python
"""Deliverable 5 -- BUJJI Live Shadow Operator, single-command entry point.

    python run_live_shadow.py [--day YYYY-MM-DD]

Workflow: acquire lock -> (login, structural only -- see Section 5 of
docs/LIVE_SHADOW_OPERATOR.md for why this environment cannot open a real
FYERS session) -> connect feed -> wait for market open -> run -> market
close -> generate reports -> shutdown.

This environment has no real FYERS credentials and no live market-hours
access (disclosed, unchanged since Sprint 104). With `--day` omitted,
this refuses to run live (fails closed, per Deliverable 3's own mandate)
and prints instructions. With `--day YYYY-MM-DD` supplied, it runs a
full SIMULATED live day against real, recorded intraday ticks and a real
Bhavcopy option chain for that day, fed tick-by-tick through the exact
same `LiveShadowOperator.process_tick` a genuine live feed would call --
this is Deliverable 8's own validation mode, reachable from the same
one-command entry point a real live run would use.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from bujji.live_shadow_operator import LiveShadowOperator, render_shadow_banner


def _run_recorded_day(day: str) -> int:
    log = logging.getLogger("bujji.run_live_shadow")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    d = day.replace("-", "")
    with open("/tmp/nifty_intraday_by_day_expanded.json") as f:
        candles = json.load(f)[day]
    with open(f"/tmp/m1/BhavCopy_NSE_FO_0_0_0_{d}_F_0000.csv") as f:
        bhav_text = f.read()

    op = LiveShadowOperator(
        lock_path="data/live_shadow_operator.lock",
        journal_dir="data/live_shadow_journal", underlying="NIFTY", logger=log,
    )
    print(render_shadow_banner())

    op.acquire()
    op.authenticate(None)  # no real broker session available in this environment
    prior_closes = op.resume_prior_closes()  # Deliverable 6: restart recovery
    op.start_session(prior_closes_with_ts=prior_closes)
    op.load_option_chain(bhav_text, day)

    for c in candles:
        op.process_tick("NIFTY", c["ts"], c["close"], source="recorded_stream")

    last_ts = candles[-1]["ts"]
    spot = next((row.underlying_price for row in op._driver._chain if row.underlying_price is not None), None)
    cadence = op.run_cadence(day=day, spot=spot, timestamp=last_ts)

    outcome = op.end_of_day(day, [cadence])
    print(outcome.report_text)
    print(render_health_dashboard_safe(op))

    op.shutdown()
    return 0


def render_health_dashboard_safe(op: LiveShadowOperator) -> str:
    from bujji.live_shadow_operator.health import render_health_dashboard
    return render_health_dashboard(op.health_snapshot())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", default=None, help="YYYY-MM-DD real recorded day to run as a simulated live session")
    args = parser.parse_args()

    if args.day is None:
        print(render_shadow_banner())
        print(
            "\nNo real FYERS credentials/live market-hours access are available in this "
            "environment (disclosed, unchanged since Sprint 104). Refusing to attempt a "
            "live connection -- fail closed, per Deliverable 3.\n\n"
            "Run a full simulated live day against real recorded data instead:\n"
            "  python run_live_shadow.py --day 2026-05-25\n"
        )
        return 1

    return _run_recorded_day(args.day)


if __name__ == "__main__":
    sys.exit(main())
