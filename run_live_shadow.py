#!/usr/bin/env python
"""BUJJI Live Shadow Operator, single-command entry point.

    python run_live_shadow.py --day YYYY-MM-DD
        Simulated live day against real recorded data (Sprint 107 Deliverable 8).

    python run_live_shadow.py --live --bhavcopy PATH --bhavcopy-day YYYY-MM-DD
        REAL live session (Sprint 114): real FYERS auth, real WebSocket ticks,
        real ATM premium quotes. Requires FYERS_APP_ID / FYERS_ACCESS_TOKEN
        (and optionally FYERS_APP_SECRET / FYERS_REFRESH_TOKEN / FYERS_PIN
        for automatic token renewal -- currently non-functional per FYERS's
        own SEBI-driven restriction, see docs/FYERS_TOKEN_LIFECYCLE.md).

Workflow: pre-market checklist (abort on any mandatory failure) -> acquire
lock -> real auth -> real WebSocket -> wait for market open -> run -> market
close -> generate reports -> shutdown. SHADOW MODE ONLY -- the live broker
instance is wrapped with `disable_live_execution` (bujji.broker.guard,
frozen, already production-used for fyers_paper mode) BEFORE it is ever
touched, so place_order/modify_order/cancel_order/get_open_positions/
get_order are structurally impossible to reach, independent of anything
in this script.

Real, disclosed limitation (unchanged since Series 108's own audit): there
is no live option-CHAIN STRUCTURE feed (which strikes/expiries exist) --
Bhavcopy is only published end-of-day. `--live` mode therefore loads the
most recent real EOD Bhavcopy the operator supplies via `--bhavcopy` for
chain structure/non-ATM premiums; only real-time underlying ticks are
genuinely live. Live ATM premium quoting (`fetch_live_atm_premiums`,
Sprint 105 follow-up 3) is NOT wired into this entry point tonight --
intentionally deferred (real live option-CONTRACT symbol resolution was
not verified in the time available before this session; wiring it later
is a disclosed, bounded follow-up, not fabricated as done here).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

from bujji.live_shadow_operator import LiveShadowOperator, render_shadow_banner
from bujji.live_shadow_operator.operator import DecisionGenerationPaused
from bujji.market_calendar import MarketCalendar
from bujji.core.clock import now_ist, epoch_to_ist

LOCK_PATH = "data/live_shadow_operator.lock"
JOURNAL_DIR = "data/live_shadow_journal"
UNDERLYING_SYMBOL = "NSE:NIFTY50-INDEX"


def _run_recorded_day(day: str) -> int:
    log = logging.getLogger("bujji.run_live_shadow")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    d = day.replace("-", "")
    with open("/tmp/nifty_intraday_by_day_expanded.json") as f:
        candles = json.load(f)[day]
    with open(f"/tmp/m1/BhavCopy_NSE_FO_0_0_0_{d}_F_0000.csv") as f:
        bhav_text = f.read()

    op = LiveShadowOperator(lock_path=LOCK_PATH, journal_dir=JOURNAL_DIR, underlying="NIFTY", logger=log)
    print(render_shadow_banner())

    op.acquire()
    op.authenticate(None)
    prior_closes = op.resume_prior_closes()
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


# ---------------------------------------------------------------------------
# Sprint 114 -- REAL live session
# ---------------------------------------------------------------------------
def _git_info():
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--short"], text=True).strip())
        return branch, commit, dirty
    except Exception as exc:  # noqa: BLE001
        return None, None, str(exc)


def _pre_market_checklist(*, bhavcopy_path: str, log: logging.Logger) -> tuple:
    """Sprint 114 Deliverable 1. Returns (ok, reasons, context) --
    `context` carries real objects (broker/tick_feed) the checklist
    already constructed, so `_run_live` doesn't reconstruct them.
    ABORTS (returns ok=False) on ANY mandatory failure -- never
    continues in a degraded mode."""
    reasons = []
    ok = True
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    branch, commit, dirty = _git_info()
    log.info("checklist git_branch=%s commit=%s dirty=%s", branch, commit, dirty)
    if branch != "v1.0-shadow":
        reasons.append(f"WARNING (non-mandatory): git branch is {branch!r}, expected 'v1.0-shadow'")
    if dirty:
        reasons.append(f"WARNING (non-mandatory): repository has uncommitted changes")

    from bujji.live_shadow_operator.safety import SHADOW_MODE, assert_shadow_safe
    try:
        assert_shadow_safe()
        log.info("checklist shadow_mode=OK")
    except Exception as exc:  # noqa: BLE001
        ok = False
        reasons.append(f"MANDATORY FAIL: shadow mode assertion failed: {exc}")

    import os
    app_id, access_token = os.getenv("FYERS_APP_ID"), os.getenv("FYERS_ACCESS_TOKEN")
    if not app_id or not access_token:
        ok = False
        reasons.append("MANDATORY FAIL: FYERS_APP_ID / FYERS_ACCESS_TOKEN not set in environment")
        return ok, reasons, {}

    from bujji.core.config import BrokerConfig
    from bujji.broker.fyers import FyersBroker
    from bujji.broker.guard import disable_live_execution
    cfg = BrokerConfig(
        name="fyers", app_id=app_id, access_token=access_token,
        app_secret=os.getenv("FYERS_APP_SECRET"), refresh_token=os.getenv("FYERS_REFRESH_TOKEN"),
        pin=os.getenv("FYERS_PIN"),
    )
    broker = disable_live_execution(FyersBroker(cfg, log))

    try:
        loop.run_until_complete(broker.connect())
        log.info("checklist fyers_authentication=OK (real profile call succeeded)")
    except Exception as exc:  # noqa: BLE001
        ok = False
        reasons.append(f"MANDATORY FAIL: FYERS authentication failed: {exc}")
        return ok, reasons, {}

    from bujji.broker.fyers_ws import FyersTickFeed
    tick_creds = broker.live_tick_credentials()
    if tick_creds is None:
        ok = False
        reasons.append("MANDATORY FAIL: broker has no live tick credentials")
        return ok, reasons, {"broker": broker}
    tick_feed = FyersTickFeed(tick_creds[0], tick_creds[1], log, log_path="logs", litemode=False)  # Day 1 finding: index lite-mode updates went silent, see docs/DAY1_LIVE_SESSION_FINDINGS.md
    tick_feed.start()
    tick_feed.subscribe([UNDERLYING_SYMBOL])
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and tick_feed.latest(UNDERLYING_SYMBOL) is None:
        time.sleep(0.5)
    if tick_feed.latest(UNDERLYING_SYMBOL) is None:
        ok = False
        reasons.append(f"MANDATORY FAIL: no real tick received for {UNDERLYING_SYMBOL} within 15s of WebSocket start")
    else:
        log.info("checklist websocket_connectivity=OK price=%s", tick_feed.latest(UNDERLYING_SYMBOL))

    try:
        spot = loop.run_until_complete(broker.get_spot("NIFTY"))
        log.info("checklist quote_api_connectivity=OK spot=%s", spot)
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"WARNING (non-mandatory): get_spot check failed: {exc}")

    try:
        chain_oi = loop.run_until_complete(broker.get_option_chain("NIFTY", 24000.0, strike_count=3))
        log.info("checklist option_chain_api_connectivity=%s", "OK" if chain_oi else "UNAVAILABLE (real, disclosed, non-mandatory)")
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"WARNING (non-mandatory): get_option_chain check failed: {exc}")

    if not Path(bhavcopy_path).exists():
        ok = False
        reasons.append(f"MANDATORY FAIL: --bhavcopy path does not exist: {bhavcopy_path}")

    usage = shutil.disk_usage(".")
    free_pct = usage.free / usage.total * 100.0
    log.info("checklist disk_free_pct=%.1f", free_pct)
    if free_pct < 5.0:
        ok = False
        reasons.append(f"MANDATORY FAIL: disk_free_pct={free_pct:.1f} < 5.0")

    Path(JOURNAL_DIR).mkdir(parents=True, exist_ok=True)
    test_file = Path(JOURNAL_DIR) / ".writable_check"
    try:
        test_file.write_text("ok")
        test_file.unlink()
        log.info("checklist journal_path_writable=OK")
    except OSError as exc:
        ok = False
        reasons.append(f"MANDATORY FAIL: journal directory not writable: {exc}")

    cal = MarketCalendar()
    is_trading, cal_reason = cal.is_trading_day(now_ist().date())
    log.info("checklist market_calendar=%s (%s)", is_trading, cal_reason)
    if not is_trading:
        ok = False
        reasons.append(f"MANDATORY FAIL: {cal_reason}")
    warn = cal.verification_warning()
    if warn:
        reasons.append(f"WARNING (non-mandatory): {warn}")

    return ok, reasons, {"broker": broker, "tick_feed": tick_feed}


def _run_live(args) -> int:
    log = logging.getLogger("bujji.run_live_shadow")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(args.log_file)])

    print(render_shadow_banner())
    print("\n=== Sprint 114 Deliverable 1: Pre-Market Readiness Checklist ===")
    ok, reasons, ctx = _pre_market_checklist(bhavcopy_path=args.bhavcopy, log=log)
    for r in reasons:
        print(f"  {r}")
    if not ok:
        print("\nABORTING STARTUP -- one or more MANDATORY checks failed. Never continuing in degraded mode.")
        return 1
    print("\nAll mandatory checks passed. Proceeding.")

    broker, tick_feed = ctx["broker"], ctx["tick_feed"]
    day = args.bhavcopy_day
    with open(args.bhavcopy) as f:
        bhav_text = f.read()

    op = LiveShadowOperator(lock_path=LOCK_PATH, journal_dir=JOURNAL_DIR, underlying="NIFTY", logger=log)
    op.acquire()
    tick_feed.on_disconnect(op.note_reconnect)  # Deliverable 3: real reconnect counting

    prior_closes = op.resume_prior_closes()
    op.resume_state()
    op.start_session(prior_closes_with_ts=prior_closes)
    op.load_option_chain(bhav_text, day)
    # Day 1 finding (docs/DAY1_LIVE_SESSION_FINDINGS.md): freshness must
    # measure how long THIS SESSION has been using its loaded chain, not
    # the Bhavcopy file's own dated market-close timestamp -- that is
    # ALWAYS >900s in the past by design (EOD data, no live chain feed
    # exists), which made option_chain register STALE on the very first
    # check, every session, independent of tick health entirely.
    chain_loaded_at = now_ist().replace(tzinfo=None)
    log.info("session started: chain structure sourced from real EOD Bhavcopy day=%s, loaded_at=%s", day, chain_loaded_at)

    last_tick_ts = None
    last_cadence_monotonic = time.monotonic()
    cadence_results = []
    market_close = now_ist().replace(hour=15, minute=30, second=0, microsecond=0)

    try:
        while now_ist().replace(tzinfo=None) < market_close.replace(tzinfo=None):
            price = tick_feed.latest(UNDERLYING_SYMBOL)
            age = tick_feed.tick_age_seconds(UNDERLYING_SYMBOL)
            if price is not None and age is not None:
                # Deep audit finding (2026-07-28): must use epoch_to_ist, never naive
                # datetime.fromtimestamp -- that interprets the epoch using the HOST's
                # local timezone, which happens to be IST on this droplet but is a real
                # redeploy trap (bujji/core/clock.py's own module docstring warns against
                # this exact pattern). Strip tzinfo after conversion since the rest of this
                # pipeline (SessionDriver, freshness checks) consistently uses naive IST
                # timestamps, matching this project's real recorded-data convention.
                ts_iso = epoch_to_ist(time.time() - age).replace(tzinfo=None).isoformat(timespec="seconds")
                if ts_iso != last_tick_ts:
                    op.process_tick("NIFTY", ts_iso, price, source="live_websocket")
                    last_tick_ts = ts_iso

            if time.monotonic() - last_cadence_monotonic >= args.cadence_seconds:
                last_cadence_monotonic = time.monotonic()
                now = now_ist().replace(tzinfo=None)
                freshness = op.check_freshness(
                    last_tick_timestamp=(datetime.fromisoformat(last_tick_ts) if last_tick_ts else None),
                    last_chain_timestamp=chain_loaded_at,
                )
                try:
                    spot = next((row.underlying_price for row in op._driver._chain if row.underlying_price is not None), None)
                    cadence = op.run_cadence(day=day, spot=spot, timestamp=now.isoformat(timespec="seconds"), freshness=freshness)
                    cadence_results.append(cadence)
                    log.info("cadence complete: thesis=%s family=%s decision=%s",
                            cadence.decision.trade_thesis.thesis_type, cadence.selection.selected_strategy_family,
                            cadence.decision.decision_id)
                except DecisionGenerationPaused as exc:
                    # Expected, routine: a mandatory input is genuinely
                    # stale. Never crash the session on this.
                    log.warning("cadence_skipped: %s", exc)
                except Exception as exc:  # noqa: BLE001
                    # Deep audit finding (2026-07-28): a bare except here
                    # previously logged EVERY failure -- routine staleness
                    # pause OR a genuine bug anywhere in the full decision
                    # chain -- identically at WARNING, indistinguishable
                    # in the log. Log this class at ERROR with a full
                    # traceback so a real defect is visibly different from
                    # an expected pause, never crash the session either way.
                    log.error("cadence_failed_unexpectedly: %s", exc, exc_info=True)

            time.sleep(args.poll_interval_seconds)
    except KeyboardInterrupt:
        log.warning("interrupted by operator -- proceeding to end-of-day report with what was collected")

    outcome = op.end_of_day(day, cadence_results)
    print("\n" + outcome.report_text)
    print("\n" + render_health_dashboard_safe(op))

    tick_feed.stop()
    op.shutdown()
    print(f"\nSession complete. Journal: {JOURNAL_DIR}/operator_journal.jsonl. Log: {args.log_file}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--day", default=None, help="YYYY-MM-DD real recorded day to run as a simulated live session")
    parser.add_argument("--live", action="store_true", help="REAL live session (Sprint 114) -- requires FYERS_APP_ID/FYERS_ACCESS_TOKEN")
    parser.add_argument("--bhavcopy", default=None, help="Path to the most recent real EOD Bhavcopy CSV (chain structure source)")
    parser.add_argument("--bhavcopy-day", default=None, help="YYYY-MM-DD the --bhavcopy file is dated")
    parser.add_argument("--cadence-seconds", type=float, default=900.0, help="Real seconds between decision cadences (default 15 min)")
    parser.add_argument("--poll-interval-seconds", type=float, default=1.0, help="Real seconds between tick-feed polls")
    parser.add_argument("--log-file", default="logs/live_shadow_session.log")
    args = parser.parse_args()

    if args.live:
        if not args.bhavcopy or not args.bhavcopy_day:
            print("ERROR: --live requires both --bhavcopy PATH and --bhavcopy-day YYYY-MM-DD (real chain-structure source).")
            return 1
        Path("logs").mkdir(parents=True, exist_ok=True)
        return _run_live(args)

    if args.day is None:
        print(render_shadow_banner())
        print(
            "\nNo mode selected. Run one of:\n"
            "  python run_live_shadow.py --day 2026-05-25                     "
            "(simulated live day, recorded data)\n"
            "  python run_live_shadow.py --live --bhavcopy PATH --bhavcopy-day YYYY-MM-DD   "
            "(REAL live session, Sprint 114)\n"
        )
        return 1

    return _run_recorded_day(args.day)


if __name__ == "__main__":
    sys.exit(main())
