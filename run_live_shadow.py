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
from bujji.broker.fyers_ws import TickSilenceWatchdog  # Sprint P1 -- tick-silence detection & forced reconnect.
from bujji.broker.paper import PaperBroker
from bujji.trading_brain.portfolio_valuation.engine import revalue
from bujji.trading_brain.portfolio_valuation.dashboard import render_portfolio_dashboard
from bujji.journal.portfolio_valuation_journal import PortfolioValuationJournal, TradeLifecycleTracker
from bujji.trading_brain.exit_engine.config import ExitRuleConfig
from bujji.trading_brain.exit_engine.engine import evaluate as evaluate_exit
from bujji.trading_brain.exit_engine.order_builder import build_closing_orders
from bujji.trading_brain.exit_engine.dashboard import render_exit_dashboard, render_exit_completion

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


def render_health_dashboard_safe(op: LiveShadowOperator, watchdog=None, tick_feed=None) -> str:
    from bujji.live_shadow_operator.health import render_health_dashboard
    # Sprint P1: real, disclosed watchdog/subscription metrics, threaded
    # through only when a real watchdog/tick_feed is supplied (the
    # `--day` replay path calls this with neither -- no live feed exists
    # there, so these stay None, exactly as before this sprint).
    kwargs = {}
    if watchdog is not None:
        kwargs.update(watchdog_state=watchdog.watchdog_state,
                      watchdog_reconnect_attempt=watchdog.reconnect_attempt,
                      watchdog_reconnect_reason=watchdog.reconnect_reason)
    if tick_feed is not None:
        kwargs["subscription_state"] = tick_feed.subscription_state
    return render_health_dashboard(op.health_snapshot(**kwargs))


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
    market_open = now_ist().replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now_ist().replace(hour=15, minute=30, second=0, microsecond=0)

    # Sprint P1: real, external tick-silence watchdog. Activates only
    # during real market hours while the feed reports itself connected
    # (its own real, disclosed activation rule) -- see
    # docs/TICK_SILENCE_INCIDENT_P1.md for why the SDK's own internal
    # reconnect path cannot be trusted to self-heal or self-report this
    # failure mode. silence_threshold_seconds is deliberately well under
    # the 900s mandatory-input STALE gate so the watchdog reacts and
    # (attempts to) recover long before a cadence would ever be skipped.
    watchdog = TickSilenceWatchdog(silence_threshold_seconds=120.0, max_consecutive_failures=3,
                                   backoff_base_seconds=30.0, logger=log)

    # Live Shadow Real-Time Paper Execution sprint: real, tick-driven
    # portfolio valuation wiring. No order-construction step exists in
    # this script yet (disclosed, unchanged here) -- paper_broker will
    # report zero open positions until that is added, so every
    # valuation below will be an honest, empty (never fabricated)
    # PortfolioValuation until real paper orders exist to revalue.
    paper_broker = PaperBroker()
    portfolio_journal = PortfolioValuationJournal(f"{JOURNAL_DIR}/portfolio_valuation.jsonl")
    trade_lifecycle_tracker = TradeLifecycleTracker()
    latest_prices: dict = {}

    # Exit Engine v1 sprint: v1 rule set, real config -- hard time exit
    # at real market close minus 15 minutes (never later than the
    # session's own hard market_close), max_loss/profit_target left
    # disabled by default (None) since this script has no real capital
    # sizing wired in yet to make a real Rupee threshold meaningful --
    # an operator running with real paper positions should set these
    # explicitly, not inherit a guessed default.
    exit_config = ExitRuleConfig(hard_time_exit="15:15", max_loss=None, profit_target=None)

    try:
        while now_ist().replace(tzinfo=None) < market_close.replace(tzinfo=None):
            price = tick_feed.latest(UNDERLYING_SYMBOL)
            age = tick_feed.tick_age_seconds(UNDERLYING_SYMBOL)

            now_naive = now_ist().replace(tzinfo=None)
            in_market_hours = market_open.replace(tzinfo=None) <= now_naive < market_close.replace(tzinfo=None)
            watchdog.check(
                tick_age=age, is_connected=tick_feed.is_connected, market_hours=in_market_hours,
                now_monotonic=time.monotonic(), force_reconnect_fn=tick_feed.force_reconnect,
            )

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

                    latest_prices[UNDERLYING_SYMBOL] = price
                    positions = asyncio.run(paper_broker.get_open_positions())
                    valuation = revalue(
                        positions, latest_prices, {}, triggering_symbol=UNDERLYING_SYMBOL,
                        triggering_tick_timestamp=ts_iso, clock=lambda: datetime.fromisoformat(ts_iso),
                    )
                    portfolio_journal.record_valuation(valuation)
                    trade_lifecycle_tracker.observe(valuation)
                    log.info("portfolio_valuation legs=%d total_pnl=%s", len(valuation.legs), valuation.total_pnl)

                    if positions:
                        now_hhmm = now_ist().strftime("%H:%M")
                        exit_decision = evaluate_exit(valuation, positions, exit_config, now_ist_time=now_hhmm)
                        log.info("exit_decision should_exit=%s reason=%s confidence=%s",
                                exit_decision.should_exit, exit_decision.reason, exit_decision.confidence)
                        if exit_decision.should_exit:
                            closing_orders = build_closing_orders(positions, valuation)
                            for closing_order in closing_orders:
                                exit_result = asyncio.run(paper_broker.place_order(closing_order))
                                symbol = closing_order.contract.symbol
                                summary = trade_lifecycle_tracker.summary(symbol)
                                portfolio_journal.record_exit(
                                    symbol=symbol,
                                    entry_ltp=(summary["entry_ltp"] if summary else None),
                                    exit_ltp=exit_result.average_price,
                                    running_mtm=(valuation.total_pnl or 0.0),
                                    max_profit=(summary["max_profit"] if summary else None),
                                    max_drawdown=(summary["max_drawdown"] if summary else None),
                                    exit_reason=exit_decision.reason,
                                    entry_timestamp=(summary["entry_timestamp"] if summary else None),
                                    exit_timestamp=ts_iso,
                                    final_mtm=paper_broker.get_realized_pnl(symbol),
                                    exit_decision_timestamp=exit_decision.timestamp,
                                )
                                log.warning("%s", render_exit_completion(
                                    symbol=symbol, exit_reason=exit_decision.reason, exit_timestamp=ts_iso,
                                    exit_price=exit_result.average_price, final_pnl=paper_broker.get_realized_pnl(symbol),
                                ).replace("\n", " | "))

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
    print("\n" + render_health_dashboard_safe(op, watchdog=watchdog, tick_feed=tick_feed))

    final_positions = asyncio.run(paper_broker.get_open_positions())
    final_valuation = revalue(
        final_positions, latest_prices, {}, triggering_symbol=None, triggering_tick_timestamp=None,
    )
    print("\n" + render_portfolio_dashboard(final_valuation))

    final_exit_decision = evaluate_exit(final_valuation, final_positions, exit_config, now_ist_time=now_ist().strftime("%H:%M"))
    print("\n" + render_exit_dashboard(final_valuation, final_exit_decision, exit_config, now_ist_time=now_ist().strftime("%H:%M")))

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
