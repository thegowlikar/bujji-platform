#!/usr/bin/env python
"""Paper Intelligence Campaign -- live intraday runner, Phase 6.

Constructs the ONE canonical intraday multi-cycle observer,
`bujji.shadow_runtime.shadow_session_runner.ShadowSessionRunner`
(unmodified this phase except for Phase 5/6's own additive
`on_cycle_evidence` hook), with all three of its own real evidence
flags on: `market_perception_enabled`, `intelligence_cycle_enabled`,
`intelligence_pipeline_enabled`. This is NOT a new runtime -- it is the
exact same class `scripts/run_shadow_live_observatory.py` already
drives in production, with one more of its own existing flags opted
in, plus the new callback.

Each real cycle, `on_cycle_evidence` receives the SAME two real objects
`ShadowSessionRunner` already builds internally every cycle when both
flags are on (`CycleEvidence` via `IntelligenceCycleRecorder.
last_evidence`, and a real `MarketIntelligenceSnapshot`) and passes
them straight into `bujji.paper_intelligence_mode.run_cycle()` --
itself pure composition of already-real, already-tested engines
(`market_thesis.assess`, `intelligence_orchestrator.orchestrate`,
`TradingSessionGovernor`'s own local `strategy_selector.select_strategy`)
-- producing one real `DecisionArtifact` per cycle, appended to a
`DecisionArtifactJournal`.

STRUCTURALLY NO EXECUTION IS POSSIBLE: this script never imports
PaperBroker, never imports TradingSessionGovernor's own stateful
session/lock lifecycle, and never calls anything under
msi_trade_construction/execution_engine/risk_governor. It is
observation and reasoning only, per this phase's explicit instruction:
"Do not implement Paper Trading execution yet."

REQUIRES a real, already-authenticated FYERS session: `source
/tmp/local_fyers.env` on the VPS (the operator's own standing step,
same as `scripts/run_phase20_13_live_entrypoint.py`) before running.

Reuses, never re-derives:
  - ATM strike resolution from the cached instrument master CSV --
    identical to `scripts/run_shadow_live_observatory.py`.
  - `bujji.shadow_runtime.manual_entrypoint_guard.
    refuse_if_authoritative_runtime_active()` -- so this script refuses
    to start if the authoritative daily runtime
    (`run_daily_intelligence_session.py`) currently owns
    `data/daily_intelligence.lock`, avoiding a duplicate broker/capture
    session -- same guard `run_shadow_live_observatory.py` already
    uses.
  - `bujji.market_calendar.MarketCalendar.is_trading_day()` -- a
    non-trading day is a clean, expected skip (exit 0), never a
    fabricated session.
  - `bujji.core.process_lock.ProcessLock` at its OWN dedicated lock
    path (`data/paper_intelligence_campaign.lock`, deliberately never
    `data/daily_intelligence.lock` or `data/shadow_decision_campaign.lock`
    -- a third, separate system per docs/SYSTEM_OWNERSHIP.md) --
    prevents two manual invocations of THIS script from colliding.
  - `_load_env_file` quote-stripping fix from Phase 20.26 (root cause
    documented in run_phase20_13_live_entrypoint.py).
"""
from __future__ import annotations

import asyncio
import csv
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, "/opt/bujji/app")
os.chdir("/opt/bujji/app")

DEFAULT_LOCK_PATH = "/opt/bujji/app/data/paper_intelligence_campaign.lock"
CACHE_FILE = "/opt/bujji/app/data/instrument_master/fyers_fo_NSE.csv"
COL_LOT_SIZE, COL_EXPIRY_EPOCH, COL_SYMBOL = 3, 8, 9
COL_UNDERLYING, COL_STRIKE, COL_OPTION_TYPE = 13, 15, 16
IST = timezone(timedelta(hours=5, minutes=30))


def _load_env_file(path: str) -> None:
    """Same quote-stripping fix as run_phase20_13_live_entrypoint.py's
    own `_load_env_file` (Phase 20.26 root-caused fix) -- reused
    verbatim, not re-derived."""
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


def _ist_now():
    return datetime.now(IST)


def _resolve_atm_offline(spot: float):
    """Identical to run_shadow_live_observatory.py's own function --
    reused, never reimplemented."""
    now_epoch = time.time()
    rows = []
    with open(CACHE_FILE) as f:
        for row in csv.reader(f):
            if len(row) <= COL_OPTION_TYPE:
                continue
            if row[COL_UNDERLYING] != "NIFTY" or row[COL_OPTION_TYPE] not in ("CE", "PE"):
                continue
            try:
                expiry_epoch = int(row[COL_EXPIRY_EPOCH])
                strike = float(row[COL_STRIKE])
                lot_size = int(row[COL_LOT_SIZE])
            except ValueError:
                continue
            if expiry_epoch < now_epoch:
                continue
            rows.append((expiry_epoch, strike, row[COL_OPTION_TYPE], row[COL_SYMBOL], lot_size))
    if not rows:
        raise RuntimeError("no unexpired NIFTY CE/PE rows found in cached instrument master")
    nearest_expiry = min(r[0] for r in rows)
    same_expiry = [r for r in rows if r[0] == nearest_expiry]
    ce_rows = [r for r in same_expiry if r[2] == "CE"]
    pe_rows = [r for r in same_expiry if r[2] == "PE"]
    atm_ce = min(ce_rows, key=lambda r: abs(r[1] - spot))
    atm_pe = min(pe_rows, key=lambda r: abs(r[1] - spot))
    expiry_date = datetime.fromtimestamp(nearest_expiry, timezone.utc).date().isoformat()
    return atm_ce, atm_pe, expiry_date


async def _main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle-interval-seconds", type=int, default=300,
                         help="5 minutes -- matches this project's other intraday cycle cadences (Phase 15Q candle store).")
    parser.add_argument("--market-open", default="09:15:00",
                         help="refuse to start before this IST time -- a pre-open "
                              "campaign reasons about a market that is not there")
    parser.add_argument("--market-close", default="15:20:00", help="stop issuing new cycles after this IST time")
    parser.add_argument("--fyers-env-file", default="/tmp/local_fyers.env")
    parser.add_argument("--lock-path", default=DEFAULT_LOCK_PATH)
    parser.add_argument("--skip-calendar-check", action="store_true",
                         help="bypass the trading-day gate -- manual/testing use only, never systemd")
    args = parser.parse_args()

    # OPEN GATE (added 2026-08-19). This launcher always bounded its END
    # (max_cycles to --market-close) but never its START: a 05:02 IST manual
    # start of the freshly installed unit ran happily and persisted a
    # DecisionArtifact reasoned against a market four hours from opening --
    # the same class of pre-open fabrication capture_market_reality_session's
    # within_market_hours() gate exists to prevent. The 09:27:30 timer never
    # hits this; manual starts and any future Persistent= replay do.
    open_time = datetime.strptime(args.market_open, "%H:%M:%S").time()
    if _ist_now().time() < open_time:
        print(f"Before market open ({_ist_now().isoformat()} IST < {args.market_open}) -- "
              "refusing to start: a pre-open campaign would reason about a market "
              "that is not there. The timer fires at 09:27:30; wait for it.",
              file=sys.stderr)
        return 1


    from bujji.market_calendar import MarketCalendar
    from bujji.shadow_runtime.manual_entrypoint_guard import (
        AuthoritativeRuntimeActiveError,
        refuse_if_authoritative_runtime_active,
    )

    session_date = _ist_now().date().isoformat()

    if not args.skip_calendar_check:
        calendar = MarketCalendar()
        warning = calendar.verification_warning()
        if warning:
            print(f"Calendar warning: {warning}", file=sys.stderr)
        is_trading, reason = calendar.is_trading_day(date.fromisoformat(session_date))
        if not is_trading:
            print(f"Skipping Paper Intelligence Campaign for {session_date}: {reason}. "
                  f"A non-trading day is a clean, expected skip -- not a failure.")
            return 0

    try:
        refuse_if_authoritative_runtime_active()
    except AuthoritativeRuntimeActiveError as exc:
        print(f"REFUSING TO START: {exc}", file=sys.stderr)
        return 1

    from bujji.core.process_lock import LockAcquisitionError, ProcessLock
    lock = ProcessLock(args.lock_path)
    try:
        lock.acquire()
    except LockAcquisitionError as exc:
        print(f"Refusing to start: {exc}", file=sys.stderr)
        return 1

    try:
        return await _run_session(args, session_date)
    finally:
        lock.release()


async def _run_session(args, session_date: str) -> int:
    from bujji.broker.fyers import FyersBroker
    from bujji.broker.guard import disable_live_execution
    from bujji.core.config import BrokerConfig
    from bujji.core.enums import OptionType, Side
    from bujji.core.models import OptionContract
    from bujji.decision_artifact import DecisionArtifactJournal
    from bujji.paper_intelligence_mode import run_cycle
    from bujji.shadow_runtime.shadow_session_runner import ShadowSessionRunner

    if not os.path.exists(args.fyers_env_file):
        print(f"FYERS credentials file not found at {args.fyers_env_file!r} -- refresh the token "
              f"(the operator's own step) before running this live. Refusing to start with no real "
              f"session rather than silently falling back to fabricated data.", file=sys.stderr)
        return 1
    _load_env_file(args.fyers_env_file)

    logger = logging.getLogger("paper_intelligence_campaign")
    broker_config = BrokerConfig(
        name="fyers", app_id=os.environ["FYERS_APP_ID"], access_token=os.environ["FYERS_ACCESS_TOKEN"],
        app_secret=os.environ.get("FYERS_APP_SECRET"), refresh_token=os.environ.get("FYERS_REFRESH_TOKEN"),
        pin=os.environ.get("FYERS_PIN"),
    )
    # Same structural guard bujji.broker.factory._build_hybrid_paper_broker
    # already applies to its own live-data-only leg -- place_order/
    # modify_order/cancel_order/get_open_positions/get_order raise
    # LiveExecutionDisabledError on THIS instance before any network call.
    broker = disable_live_execution(FyersBroker(broker_config, logger))
    await broker.connect()

    spot = await broker.get_spot("NIFTY")
    atm_ce, atm_pe, expiry_date = _resolve_atm_offline(spot)
    ce = OptionContract(atm_ce[3], "NIFTY", int(atm_ce[1]), OptionType.CE, expiry_date, atm_ce[4])
    pe = OptionContract(atm_pe[3], "NIFTY", int(atm_pe[1]), OptionType.PE, expiry_date, atm_pe[4])
    watchlist = [(ce, Side.SELL), (pe, Side.SELL)]

    session_id = f"PAPER-INTELLIGENCE-{session_date}"
    session_dir = f"paper_intelligence_sessions/{session_id}"
    os.makedirs(session_dir, exist_ok=True)
    logging.basicConfig(
        filename=f"{session_dir}/session.log", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    journal = DecisionArtifactJournal(f"{session_dir}/decision_artifacts.jsonl")
    cycle_errors = []

    def on_cycle_evidence(evidence, perception_snapshot):
        if evidence is None:
            return  # a cycle whose market-perception step itself failed -- nothing to reason over.
        try:
            artifact = run_cycle(
                evidence, session_id=session_id, perception_snapshot=perception_snapshot,
                clock=lambda: datetime.now(timezone.utc),
            )
            journal.append(artifact)
        except Exception as exc:  # noqa: BLE001 -- one cycle's reasoning failure must never break the live loop.
            cycle_errors.append(f"paper_intelligence_mode_failed: {type(exc).__name__}: {exc}")

    close_time = datetime.strptime(args.market_close, "%H:%M:%S").time()
    close_dt = _ist_now().replace(hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0)
    remaining = max(0.0, (close_dt - _ist_now()).total_seconds())
    max_cycles = max(1, int(remaining // args.cycle_interval_seconds))

    runner = ShadowSessionRunner(
        broker=broker, watchlist=watchlist, storage_path=f"{session_dir}/quotes.jsonl",
        session_id=session_id, clock=lambda: datetime.now(timezone.utc),
        max_cycles=max_cycles, max_consecutive_failures=5,
        sleep_seconds=args.cycle_interval_seconds,
        market_perception_enabled=True,
        market_snapshot_path=f"{session_dir}/market_snapshots.jsonl",
        intelligence_snapshot_path=f"{session_dir}/intelligence_snapshots.jsonl",
        intelligence_cycle_enabled=True,
        intelligence_cycle_path=f"{session_dir}/intelligence_cycle.jsonl",
        intelligence_pipeline_enabled=True,
        intelligence_pipeline_event_store_path=f"{session_dir}/intelligence_pipeline_events.jsonl",
        health_path=f"{session_dir}/health.json", session_date=session_date,
        on_cycle_evidence=on_cycle_evidence,
    )

    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    import signal
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, main_task.cancel)
        except NotImplementedError:  # pragma: no cover -- non-POSIX platforms.
            pass

    try:
        artifact = await runner.start()
    except asyncio.CancelledError:
        print(f"\nGraceful shutdown requested -- {len(journal.read_all())} decision artifacts already persisted to "
              f"{session_dir}/decision_artifacts.jsonl.")
        return 0

    print(f"\nsession_id={artifact.session_id} heartbeats={artifact.runtime_health.get('heartbeats')} "
          f"errors={list(artifact.errors)} decision_artifacts={len(journal.read_all())} "
          f"paper_intelligence_errors={cycle_errors}")
    return 0 if not artifact.errors else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
