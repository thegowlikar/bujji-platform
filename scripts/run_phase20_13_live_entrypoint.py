#!/usr/bin/env python
"""Phase 20.13 -- Live shadow session entrypoint (wiring, not a new
runtime). Wires this phase's own `bujji.live_shadow_runner` day-loop as
`bujji.shadow_runtime.daily_session.DailySessionRuntime`'s injected
`intelligence_fn`, exactly as the phase report's own Limitations
section (and `bujji/live_shadow_runner/__init__.py`'s own architecture
disclosure) describes. `DailySessionRuntime` supplies the outer
lifecycle, heartbeat, and graceful shutdown -- unmodified, Phase 19.11.
Nothing in this file recomputes any prior phase's own logic.

REAL FEED WIRING: `candle_fetch_fn`/`vix_fetch_fn` are backed by
`bujji.broker.fyers.FyersBroker.get_recent_candles()`/`.get_vix()` --
the SAME read-only methods `bujji.market_perception.market_data_adapter.
MarketDataAdapter` already wraps for the rest of this codebase, never
reimplemented here. The broker instance is passed through `bujji.
broker.guard.disable_live_execution()` immediately on construction --
the SAME structural guard `bujji.broker.factory._build_hybrid_paper_broker`
already uses for its own live-*data*-only leg -- so `place_order`/
`modify_order`/`cancel_order`/`get_open_positions`/`get_order` raise
`LiveExecutionDisabledError` if ever called, on this specific instance,
before any network call. Trailing VIX history comes from `bujji.
historical_reality.store.HistoricalObservationStore`'s own real, already-
captured daily closes (Phase 15Q onward) -- never fabricated, never a
second live call for data this codebase already has real values for.

REQUIRES a real, already-authenticated FYERS session: `source
/tmp/local_fyers.env` on the VPS (this project's own standing
operational pattern -- refreshing the token is the operator's own step,
never performed by this script) before running. Read-only market data
calls ONLY -- no order, no position, no broker write call exists
anywhere in this file or in anything it imports from `bujji.
live_shadow_runner`.

Still SHADOW ONLY: this entrypoint records `DecisionObservation`/
`CampaignSession`/`CampaignMetrics`/`HealthReport` artifacts only. It
never places, modifies, or cancels an order, and never constructs a
position or P&L record.

PHASE 20.14 ADDITIONS -- operationalization, no new runtime. Two real,
already-existing mechanisms are wired in, exactly as
`run_daily_intelligence_session.py` already wires them for the
unrelated Phase 19.x runtime, never reimplemented:

  `bujji.core.process_lock.ProcessLock` at a DEDICATED lock path
  (default `data/shadow_decision_campaign.lock` -- deliberately never
  `data/daily_intelligence.lock`, since that lock belongs to a separate
  system per `docs/SYSTEM_OWNERSHIP.md`). Acquired before any broker
  connection, released on any exit path (including a crash or SIGTERM),
  so a systemd timer overlap or an accidental second manual invocation
  fails fast instead of running two live sessions concurrently.

  `bujji.market_calendar.MarketCalendar.is_trading_day()` -- checked
  before any broker connection. A non-trading day (weekend or listed
  NSE holiday) is a clean, expected skip (exit 0, no error, no
  fabricated session), never treated as a failure.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, datetime, time as dtime
from typing import List, Optional

sys.path.insert(0, "/opt/bujji/app")

DEFAULT_LOCK_PATH = "/opt/bujji/app/data/shadow_decision_campaign.lock"


def _load_env_file(path: str) -> None:
    """Same convention `scripts/run_shadow_live_observatory.py` already
    uses -- parses `KEY=value` lines from the operator's own refreshed
    credentials file into `os.environ`, never hardcoded, never logged.

    Strips exactly one matching pair of wrapping quotes ('value' or
    "value") after whitespace-stripping -- shell-style quoting the
    operator's own refresh tooling writes into this file. Internal
    quotes (not at both ends) are left untouched. Root cause (Phase
    20.26 live validation, 2026-08-17): `.strip()` alone left the
    literal quote characters IN the token string, corrupting the
    `Authorization: {app_id}:{token}` header FyersModel sends and
    causing a real, valid, unexpired token to be rejected by FYERS
    with code=-17 "Could not authenticate the user" -- confirmed by a
    direct, isolated before/after `connect()` comparison."""
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ[key.strip()] = value


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument("--session-date", help="YYYY-MM-DD, real NSE trading day")
    date_group.add_argument("--date-today", action="store_true",
                             help="use today's real date -- for systemd, which cannot hardcode a date")
    parser.add_argument("--heartbeat-path", default=None)
    parser.add_argument("--artifact-path", default=None)
    parser.add_argument("--lock-path", default=DEFAULT_LOCK_PATH)
    parser.add_argument("--skip-calendar-check", action="store_true",
                         help="bypass the trading-day gate -- manual/testing use only, never systemd")
    parser.add_argument("--cycle-interval-minutes", type=int, default=5)
    parser.add_argument("--market-open", default="09:15:00")
    parser.add_argument("--market-close", default="15:30:00")
    parser.add_argument("--fyers-env-file", default="/tmp/local_fyers.env")
    parser.add_argument("--historical-store-path",
                         default="/opt/bujji/app/data/historical_reality/normalized/historical_observations.db")
    # Default must be >= mic_v0.volatility_classifier.MIN_WINDOW (60) --
    # a lower default silently starves the classifier of enough trailing
    # history to ever produce a real percentile (Task #166: the classifier
    # requires 60 samples, this used to request only 30, so it always fell
    # back to "insufficient_history" -- effectively blind, not disabled).
    parser.add_argument("--trailing-vix-days", type=int, default=60)
    args = parser.parse_args()
    session_date = args.session_date if args.session_date else date.today().isoformat()

    from bujji.core.process_lock import LockAcquisitionError, ProcessLock
    from bujji.market_calendar import MarketCalendar

    if not args.skip_calendar_check:
        calendar = MarketCalendar()
        is_trading, reason = calendar.is_trading_day(date.fromisoformat(session_date))
        warning = calendar.verification_warning()
        if warning:
            print(f"Calendar warning: {warning}")
        if not is_trading:
            print(f"Skipping shadow decision campaign for {session_date}: {reason}. "
                  f"A non-trading day is a clean, expected skip -- not a failure.")
            return 0

    lock = ProcessLock(args.lock_path)
    try:
        lock.acquire()
    except LockAcquisitionError as exc:
        print(f"Refusing to start: {exc}")
        return 1

    try:
        return await _run_session(args, session_date)
    finally:
        lock.release()


async def _run_session(args: argparse.Namespace, session_date: str) -> int:
    from bujji.broker.fyers import FyersBroker
    from bujji.broker.guard import disable_live_execution
    from bujji.broker.option_market_data import fetch_option_market_data_for_cycle
    from bujji.core.config import BrokerConfig
    from bujji.historical_reality.store import HistoricalObservationStore
    from bujji.intelligence.context import EXECUTION_MODE_LIVE
    from bujji.shadow_decision_runtime import ShadowDecisionLog
    from bujji.shadow_market_campaign import build_campaign_report, build_campaign_session, validate_session_behavior
    from bujji.shadow_runtime.daily_session import (
        CaptureResult, DailySessionRuntime, IntelligenceRunResult,
    )
    from bujji.strategy_intelligence import StrategyEvidence
    from bujji.live_shadow_runner import (
        ShadowRunConfig, close_session, evaluate_runtime_health,
        process_cycle, save_campaign_artifact, start_session,
    )

    if not os.path.exists(args.fyers_env_file):
        print(f"FYERS credentials file not found at {args.fyers_env_file!r} -- refresh the token "
              f"(the operator's own step) before running this live. Refusing to start with no real "
              f"session rather than silently falling back to fabricated data.")
        return 1
    _load_env_file(args.fyers_env_file)

    broker_config = BrokerConfig(
        name="fyers", app_id=os.environ["FYERS_APP_ID"], access_token=os.environ["FYERS_ACCESS_TOKEN"],
        app_secret=os.environ.get("FYERS_APP_SECRET"), refresh_token=os.environ.get("FYERS_REFRESH_TOKEN"),
        pin=os.environ.get("FYERS_PIN"),
    )
    logger = logging.getLogger("phase20_13_live_entrypoint")
    # The SAME structural guard bujji.broker.factory._build_hybrid_paper_broker
    # already applies to its own live-data-only leg -- neuters place_order/
    # modify_order/cancel_order/get_open_positions/get_order on THIS instance,
    # before it is handed to anything else in this script.
    broker = disable_live_execution(FyersBroker(broker_config, logger))
    await broker.connect()

    historical_store = HistoricalObservationStore(args.historical_store_path)

    artifact_path = args.artifact_path or f"/opt/bujji/app/data/live_shadow_campaign/{session_date}.jsonl"

    TREND_FAVORABLE, TREND_UNFAVORABLE = ("TREND_UP", "TREND_DOWN"), ("RANGE",)
    MR_FAVORABLE, MR_UNFAVORABLE = ("RANGE",), ("TREND_UP", "TREND_DOWN")
    trend_evidence = StrategyEvidence(
        strategy_name="FAMILY_A_TREND_FOLLOWING", sample_size=11278, win_rate=0.665, profit_factor=2.30,
        net_expectancy=517.0, gross_expectancy=949.0,
        train_expectancy=512.0, validation_expectancy=481.0, out_of_sample_expectancy=573.0,
    )
    mr_evidence = StrategyEvidence(
        strategy_name="FAMILY_B_MEAN_REVERSION", sample_size=150, win_rate=0.0, profit_factor=0.0,
        net_expectancy=-1664.0, gross_expectancy=-1218.0,
        train_expectancy=-1375.0, validation_expectancy=-1806.0, out_of_sample_expectancy=-2260.0,
    )
    strategies = ((trend_evidence, TREND_FAVORABLE, TREND_UNFAVORABLE), (mr_evidence, MR_FAVORABLE, MR_UNFAVORABLE))

    # Real trailing VIX history -- Phase 15Q's own already-captured daily
    # closes, strictly before today (no look-ahead, and no reason to spend
    # a live call re-fetching values this codebase already has real,
    # certified values for). Computed once; the same trailing window is
    # valid for every cycle in today's session.
    vix_rows = historical_store.range(
        "NSE:INDIAVIX-INDEX", "DAILY", "2008-01-01T00:00:00+05:30", f"{session_date}T00:00:00+05:30",
    )
    trailing_vix = [r.payload["close"] for r in
                     sorted(vix_rows, key=lambda r: r.observation.identity.timestamp)][-args.trailing_vix_days:]
    if not trailing_vix:
        print(f"No real trailing VIX history found before {session_date} in "
              f"{args.historical_store_path!r} -- refusing to start rather than fabricate one.")
        historical_store.close()
        return 1

    async def candle_fetch_fn(as_of: datetime) -> List:
        """The LATEST real NIFTY 5-minute candles, via
        `FyersBroker.get_recent_candles()` (read-only, the SAME method
        `MarketDataAdapter` already wraps) -- never reimplemented, never
        fabricated.

        `as_of` IS NOT HONOURED and cannot be: `get_recent_candles` takes a
        count, not a point in time, and always returns bars ending at the
        wall clock. This docstring previously claimed "ending at or before
        `as_of`", which is what allowed the caller to label cycles with a
        virtual clock and believe the data followed it. The caller now
        stamps real wall-clock time instead; the parameter is retained only
        to satisfy the fetch-function signature the runtime expects."""
        return await broker.get_recent_candles("NIFTY", minutes=5, count=75)

    async def vix_fetch_fn(as_of: datetime):
        """Real CURRENT India VIX level via `FyersBroker.get_vix()`
        (read-only, LIVE-VERIFIED per that method's own docstring).
        `trailing_vix` is the real, already-captured history computed
        once above -- never refetched or fabricated per cycle.

        `as_of` is not honoured here either: `get_vix()` returns the live
        level and has no historical form. See candle_fetch_fn."""
        vix_data = await broker.get_vix()
        if vix_data is None or vix_data.get("level") is None:
            raise RuntimeError("FyersBroker.get_vix() returned no real level -- "
                                "refusing to fabricate a VIX reading for this cycle.")
        return float(vix_data["level"]), trailing_vix

    async def capture_fn() -> CaptureResult:
        # Cycle 1's own design fuses capture-then-classify inside each
        # process_cycle() call (compose_market_state, Phase 20.1) --
        # there is no separate capture step here to wrap, matching this
        # package's own disclosed design decision (see __init__.py).
        return CaptureResult(rows_captured=0, last_observation_timestamp=None)

    async def intelligence_fn() -> IntelligenceRunResult:
        config = ShadowRunConfig(
            market="NIFTY", session_date=session_date,
            cycle_interval_minutes=args.cycle_interval_minutes, data_source="live_fyers_read_only",
        )
        state = start_session(config)
        log = ShadowDecisionLog()
        errors: List[str] = []

        open_time = dtime.fromisoformat(args.market_open)
        close_time = dtime.fromisoformat(args.market_close)
        open_dt = datetime.combine(datetime.fromisoformat(session_date).date(), open_time).astimezone()
        close_dt = datetime.combine(datetime.fromisoformat(session_date).date(), close_time).astimezone()

        # WALL-CLOCK TRUTH (corrected 2026-08-17).
        #
        # `now` used to be a VIRTUAL clock: initialised to open_dt and
        # advanced by exactly the sleep interval, so cycle k was always
        # stamped 09:15 + k*5min NO MATTER WHEN THE PROCESS STARTED, while
        # the data in it came from whenever the fetch actually ran. Observed
        # on 2026-08-17: the process started at 09:01:47 and its first
        # artifact was stamped 2026-08-17T09:15:00+05:30 -- a ~13 minute
        # lie, with the early cycles describing a market that had not yet
        # opened. Every artifact this unit has ever written carries that
        # offset.
        #
        # The fetch functions CANNOT honour an `as_of` -- get_recent_candles
        # and get_vix return the latest real values, nothing else (their
        # docstrings below now say so). Given that, the only label that can
        # be true is the real time at which the data was actually read.
        # Stamping wall-clock time makes label and content agree by
        # construction, and -- unlike pinning the timer to fire at exactly
        # 09:15 -- it stays true however late the unit starts and however
        # much per-cycle drift accumulates over a 6-hour session.
        def _wall_now() -> datetime:
            return datetime.now(open_dt.tzinfo)

        # Never observe before the open. A pre-market read stamped as a
        # market cycle is precisely the fabrication this codebase forbids
        # everywhere else; waiting costs nothing and cannot mislead.
        seconds_until_open = (open_dt - _wall_now()).total_seconds()
        if seconds_until_open > 0:
            print(f"Waiting {seconds_until_open:.0f}s for the {open_dt.isoformat()} "
                  f"market open before the first observation cycle.")
            await asyncio.sleep(seconds_until_open)

        if _wall_now() > close_dt:
            errors.append(
                f"started at {_wall_now().isoformat()}, after the "
                f"{close_dt.isoformat()} close -- no cycle observed. Reported "
                "as an empty session rather than a backfilled one.")

        while True:
            now = _wall_now()
            if now > close_dt:
                break
            try:
                candles = await candle_fetch_fn(now)
                current_vix, trailing_vix = await vix_fetch_fn(now)
                # Phase 20.26 -- best-effort real option-chain/quote fetch.
                # `None` on ANY missing/invalid real value (never fabricated)
                # -- `process_cycle`'s own documented default, Phase 20.24's
                # own behavior preserved exactly when this stays `None`.
                option_market_data = None
                try:
                    spot = await broker.get_spot("NIFTY")
                    option_market_data = await fetch_option_market_data_for_cycle(
                        broker, "NIFTY", spot, now=now,
                    )
                except Exception as opt_exc:  # noqa: BLE001 -- option data is best-effort, never fatal to the cycle.
                    errors.append(f"option_market_data_error@{now.isoformat()}: {type(opt_exc).__name__}: {opt_exc}")
                state, observations = process_cycle(
                    state, now.isoformat(), candles, current_vix, trailing_vix, strategies, log,
                    execution_mode=EXECUTION_MODE_LIVE, option_market_data=option_market_data,
                )
                for obs in observations:
                    save_campaign_artifact(artifact_path, "DecisionObservation", obs)
            except Exception as exc:  # noqa: BLE001 -- one bad cycle must never stop the whole session.
                errors.append(f"cycle_error@{now.isoformat()}: {type(exc).__name__}: {exc}")
            # No manual advance: the next iteration re-reads the real
            # clock, so a slow cycle shortens the session rather than
            # silently back-dating every subsequent label.
            await asyncio.sleep(args.cycle_interval_minutes * 60)

        state = close_session(state)
        health = evaluate_runtime_health(state, log.observations, close_dt.isoformat())
        save_campaign_artifact(artifact_path, "HealthReport", health)

        session = build_campaign_session(
            session_date, open_dt.isoformat(), close_dt.isoformat(), log,
        )
        metrics = validate_session_behavior(session, log.observations)
        save_campaign_artifact(artifact_path, "CampaignSession", session)
        save_campaign_artifact(artifact_path, "CampaignMetrics", metrics)
        print(build_campaign_report(session, metrics))

        return IntelligenceRunResult(
            cycles_completed=state.cycles_completed,
            last_intelligence_cycle_timestamp=state.last_cycle_timestamp,
            errors=tuple(errors),
        )

    runtime = DailySessionRuntime(
        session_date=session_date, clock=datetime.now, capture_fn=capture_fn,
        intelligence_fn=intelligence_fn, heartbeat_path=args.heartbeat_path,
    )
    try:
        report = await runtime.run()
    finally:
        historical_store.close()
    print(f"\nDailySessionRuntime final_stage={report.final_stage} errors={list(report.errors)}")
    return 0 if report.final_stage != "FAILED" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
