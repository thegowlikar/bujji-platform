#!/usr/bin/env python
"""Bujji Daily Intelligence Session -- the single authoritative daily
runtime entrypoint, Phase 19.11/19.12.

    python run_daily_intelligence_session.py --date YYYY-MM-DD [--dry-run]
    python run_daily_intelligence_session.py --date-today [--dry-run]

Phase 19.12 additions over Phase 19.11's own version: single-instance
locking (reuses `bujji.core.process_lock.ProcessLock`, unmodified --
the same crash-safe `flock`-based lock `live_shadow_operator` already
uses, Phase 19.9.6's own confirmed finding), and graceful SIGTERM/SIGINT
handling for a clean systemd stop.

Orchestrates, via `bujji.shadow_runtime.daily_session.DailySessionRuntime`
(unmodified, imported): start observation capture -> start shadow
intelligence runtime -> monitor health -> finalize. This script itself
contains ZERO capture logic and ZERO intelligence logic -- it only
constructs the two real, already-existing pieces `DailySessionRuntime`
needs and hands them in, exactly like `run_live_shadow.py` (a separate,
unrelated system, per Phase 19.9.6's own confirmed finding) wires its
own already-existing decision chain.

REAL, DISCLOSED LIMITATION (unchanged since Phase 19.11): `_real_capture_fn`
below subprocess-invokes the already-existing, unmodified
`capture_market_reality_session.py`/`capture_options_reality_session.py`/
`run_futures_depth_poller.py` scripts (never re-implements their logic)
and measures real rows captured via `HistoricalObservationStore.count()`
before/after -- no new counting logic either.

Phase 19.13 UPDATE: `_real_intelligence_fn` is now wired to a real
live intelligence cycle (`bujji.shadow_runtime.live_intelligence_cycle.run_live_intelligence_cycle`,
Phase 19.13) against a real, authenticated FYERS connection -- broker
construction below is the EXACT SAME pattern `run_live_shadow.py`
already uses in production (`disable_live_execution(FyersBroker(cfg, log))`,
`bujji.broker.guard`), so `place_order`/`modify_order`/`cancel_order`/
position/margin/funds calls remain structurally unreachable regardless
of what this script does -- never re-implemented, never loosened. If
`FYERS_APP_ID`/`FYERS_ACCESS_TOKEN` are not set in the environment, this
function fails loudly with an honest error rather than silently
skipping the cycle or fabricating a result -- the same "disclosed, not
hidden" limitation this module has followed since Phase 19.11.
`--dry-run` uses safe, no-op functions that touch no broker, no
subprocess, and no lock, for manual verification of the orchestration
wiring itself.

Phase 19.14.1 UPDATE: `_make_real_completeness_fn` wires the already-
existing, unmodified `bujji.shadow_runtime.completeness.validate_end_of_day_completeness()`
(Phase 19.11) into this entrypoint for the first time -- never
reimplemented. `_make_real_intelligence_fn` now also requests the
LIVE/REPLAY equivalence check inside `run_live_intelligence_cycle`
(`include_replay_equivalence=True`) -- also Phase 19.13's own
canonical, unmodified mechanism, never a second one. Both are surfaced
on `DailySessionRuntime`'s report/heartbeat (Phase 19.14.1's own
additive `daily_session.py` fields).

Phase 19.16 UPDATE: wires the already-existing, unmodified
`bujji.market_calendar.MarketCalendar` (Sprint 112 Deliverable 6, never
touched by this change) into `_main()` -- a real, forensic-audit-found
gap: this entrypoint previously had no trading-calendar awareness at
all, so a systemd timer firing on an NSE holiday would have attempted a
real capture/intelligence cycle that could only ever report itself
honestly incomplete after the fact, never proactively skip. On a
non-trading day (weekend, listed holiday, or manual closure),
`_main()` now writes one honest heartbeat with
`runtime_status="NON_TRADING_DAY"` and returns 0 -- success, because
correctly doing nothing on a non-trading day IS success -- without ever
acquiring the lock, touching the broker, or invoking
`DailySessionRuntime`. `--dry-run` is deliberately NOT gated by the
calendar (its own purpose is exercising the orchestration wiring on
demand, any day) -- unchanged. `MarketCalendar.HOLIDAY_CALENDAR` ships
as an empty, honestly-disclosed template
(`holiday_calendar_verified=False` by the class's own default) --
`_main()` surfaces `MarketCalendar.verification_warning()` to stderr
every real (non-dry-run) invocation as a loud, recurring reminder that
an operator must populate and verify the real NSE holiday list for this
gate to catch anything beyond weekends; nothing about that disclosed
limitation is hidden or worked around here.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from bujji.core.process_lock import LockAcquisitionError, ProcessLock
from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_calendar import MarketCalendar
from bujji.shadow_runtime.daily_session import (
    CaptureResult,
    CompletenessCheckResult,
    DailySessionHeartbeat,
    DailySessionRuntime,
    IntelligenceRunResult,
    write_daily_heartbeat,
)
from bujji.state_persistence.store import EventStore

HISTORICAL_STORE_PATH = "data/historical_reality/normalized/historical_observations.db"
INTELLIGENCE_UNDERLYING = "NIFTY"


def _make_dry_run_capture_fn():
    async def capture_fn() -> CaptureResult:
        return CaptureResult(rows_captured=0, last_observation_timestamp=None, errors=("dry-run: capture skipped",))
    return capture_fn


def _make_dry_run_intelligence_fn():
    async def intelligence_fn() -> IntelligenceRunResult:
        return IntelligenceRunResult(cycles_completed=0, last_intelligence_cycle_timestamp=None, errors=("dry-run: intelligence skipped",))
    return intelligence_fn


def _make_real_capture_fn(date_str: str):
    """Subprocess-invokes the already-existing, unmodified capture
    scripts -- never re-implements their logic. Real row count measured
    via `HistoricalObservationStore.count()`, before and after, the same
    real store every other Phase 18/19 component reads."""
    async def capture_fn() -> CaptureResult:
        store = HistoricalObservationStore(HISTORICAL_STORE_PATH)
        before = store.count()
        errors = []
        # CONCURRENT, NOT SEQUENTIAL (fixed 2026-08-18). Both capture scripts
        # poll until their own MARKET_CLOSE -- neither is given --cycles -- so
        # each runs ~6h20m. The previous blocking `subprocess.run` in a for-loop
        # meant the SECOND script could never capture anything: the first
        # returned only at 15:40, and the second then aborted immediately on its
        # own within_market_hours() gate.
        #
        # The defect was invisible for as long as the first script happened to
        # fail fast. That is exactly what happened on 2026-08-18: spot/vix died
        # at 09:16:00 on a PermissionError, which released the options capture to
        # run the full day (18,450 rows). Repairing that PermissionError without
        # this change would have inverted the loss -- spot/vix captured, options
        # starved -- and looked like a fresh regression.
        #
        # Launch every child FIRST, then wait: the comprehension below must not
        # be fused into the wait loop.
        procs = [
            (script, subprocess.Popen(
                [sys.executable, script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
            for script in ("scripts/capture_market_reality_session.py",
                           "scripts/capture_options_reality_session.py")
        ]
        for script, proc in procs:
            _stdout, stderr = proc.communicate()
            if proc.returncode != 0:
                errors.append(f"{script} exited {proc.returncode}: {stderr[-500:]}")
        after = store.count()
        return CaptureResult(
            rows_captured=max(0, after - before),
            last_observation_timestamp=datetime.now(timezone.utc).isoformat() if not errors else None,
            errors=tuple(errors),
        )
    return capture_fn


def _build_real_broker(log: logging.Logger):
    """The exact broker-construction pattern `run_live_shadow.py`
    already uses in production -- reused verbatim, not re-derived.
    `disable_live_execution` (`bujji.broker.guard`) wraps the broker
    BEFORE it is ever touched, so this function can never return
    something capable of placing/modifying/cancelling an order."""
    from bujji.broker.fyers import FyersBroker
    from bujji.broker.guard import disable_live_execution
    from bujji.core.config import BrokerConfig

    app_id, access_token = os.getenv("FYERS_APP_ID"), os.getenv("FYERS_ACCESS_TOKEN")
    if not app_id or not access_token:
        raise RuntimeError("FYERS_APP_ID / FYERS_ACCESS_TOKEN not set in environment")
    cfg = BrokerConfig(
        name="fyers", app_id=app_id, access_token=access_token,
        app_secret=os.getenv("FYERS_APP_SECRET"), refresh_token=os.getenv("FYERS_REFRESH_TOKEN"),
        pin=os.getenv("FYERS_PIN"),
    )
    return disable_live_execution(FyersBroker(cfg, log))


def _make_real_intelligence_fn(
    date_str: str, *, cycle_artifact_store_path: str, daily_artifact_store_path: str,
):
    """Phase 19.13 -- wired to a real live intelligence cycle. One
    cycle per daily invocation (this entrypoint's own granularity,
    Phase 19.11 onward) -- intraday multi-cycle observation is
    `ShadowSessionRunner`'s own, separate responsibility, unmodified
    and untouched by this function."""
    async def intelligence_fn() -> IntelligenceRunResult:
        from bujji.intelligence.context import EXECUTION_MODE_LIVE
        from bujji.shadow_runtime.live_intelligence_cycle import run_live_intelligence_cycle

        log = logging.getLogger("bujji.run_daily_intelligence_session")
        try:
            broker = _build_real_broker(log)
        except RuntimeError as exc:
            return IntelligenceRunResult(cycles_completed=0, last_intelligence_cycle_timestamp=None, errors=(str(exc),))

        try:
            await broker.connect()
        except Exception as exc:  # noqa: BLE001 -- broker IO, must never raise into the caller.
            return IntelligenceRunResult(
                cycles_completed=0, last_intelligence_cycle_timestamp=None,
                errors=(f"broker_connect_failed: {type(exc).__name__}: {exc}",),
            )

        clock = lambda: datetime.now(timezone.utc)
        cycle_artifact_store = EventStore(cycle_artifact_store_path)
        daily_artifact_store = EventStore(daily_artifact_store_path)

        result = await run_live_intelligence_cycle(
            broker=broker, clock=clock, underlying=INTELLIGENCE_UNDERLYING,
            session_id=f"daily-{date_str}", cycle_id=f"daily-{date_str}-1", session_date=date_str,
            execution_mode=EXECUTION_MODE_LIVE,
            cycle_artifact_store=cycle_artifact_store, daily_artifact_store=daily_artifact_store,
            include_replay_equivalence=True,
        )

        if not result.succeeded:
            reason = result.error if result.error else f"completeness_gate_failed: {list(result.gate_reasons)}"
            return IntelligenceRunResult(cycles_completed=0, last_intelligence_cycle_timestamp=None, errors=(reason,))

        return IntelligenceRunResult(
            cycles_completed=1, last_intelligence_cycle_timestamp=result.as_of_time, errors=(),
            replay_equivalent=result.replay_equivalence.equivalent if result.replay_equivalence is not None else None,
            replay_mismatches=result.replay_equivalence.mismatches if result.replay_equivalence is not None else (),
            replay_check_error=result.replay_check_error,
        )

    return intelligence_fn


def _make_real_completeness_fn(date_str: str):
    """Phase 19.14.1, corrected in Phase 19.14.4. Wires the already-
    existing `bujji.shadow_runtime.completeness.validate_end_of_day_completeness()`
    (Phase 19.11) into the daily runtime -- reads the same
    `HISTORICAL_STORE_PATH` every capture step already writes to, never a
    second store, never re-derived logic.

    Phase 19.14.4 fix: passes `resolution=RESOLUTION_FIVE_MINUTE` and a
    real end-of-day `as_of_time` (market close, 15:40 IST -- the same
    `MARKET_CLOSE` constant `capture_market_reality_session.py` itself
    uses) instead of the previous implicit `RESOLUTION_DAILY` default.
    Phase 19.14.3's own production re-verification proved the old,
    unqualified call always found zero rows: the only writers
    (`capture_market_reality_session.py`/`capture_options_reality_session.py`)
    always write `resolution=FIVE_MINUTE`, never `DAILY` -- so every
    real, successfully-captured trading day was reported EMPTY. This is
    the smallest fix: pass the resolution/as_of_time the real data
    actually uses, not a new completeness mechanism."""
    async def completeness_fn() -> CompletenessCheckResult:
        from bujji.market_reality_snapshot.models import RESOLUTION_FIVE_MINUTE
        from bujji.shadow_runtime.completeness import validate_end_of_day_completeness

        store = HistoricalObservationStore(HISTORICAL_STORE_PATH)
        now = datetime.now(timezone.utc)
        as_of_time = f"{date_str}T15:40:00+05:30"  # NSE market close, IST -- matches MARKET_CLOSE in capture_market_reality_session.py.
        report = validate_end_of_day_completeness(
            date_str, historical_store=store, now=now,
            resolution=RESOLUTION_FIVE_MINUTE, as_of_time=as_of_time,
        )
        missing = tuple(
            name for name, present in (
                ("spot", report.spot_present), ("options", report.options_present), ("vix", report.vix_present),
            ) if not present
        )
        return CompletenessCheckResult(
            ran=True, is_complete=report.is_complete, completeness_status=report.completeness,
            missing_components=missing, as_of=now.isoformat(),
        )

    return completeness_fn


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, main_task: "asyncio.Task") -> None:
    """Phase 19.12 -- Graceful Shutdown. SIGTERM (systemd stop / VPS
    shutdown) and SIGINT (manual Ctrl-C) both cancel the running task
    cleanly -- `DailySessionRuntime.run()`'s own `except asyncio.CancelledError`
    branch (Phase 19.12) then finalizes a real terminal report and
    heartbeat rather than the process dying mid-write."""
    import signal
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, main_task.cancel)
        except NotImplementedError:  # pragma: no cover -- non-POSIX platforms.
            pass


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument("--date", help="YYYY-MM-DD")
    date_group.add_argument("--date-today", action="store_true", help="use today's real date -- for systemd, which cannot hardcode a date")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--heartbeat-path", default="data/daily_session_heartbeat.json")
    parser.add_argument("--lock-path", default="data/daily_intelligence.lock")
    parser.add_argument("--cycle-artifact-store-path", default="data/shadow_intelligence_cycle_artifacts.jsonl")
    parser.add_argument("--daily-artifact-store-path", default="data/daily_intelligence_artifacts.jsonl")
    args = parser.parse_args()

    session_date = args.date if args.date else date.today().isoformat()

    # Phase 19.16 -- trading-calendar gate. Real (non-dry-run) invocations
    # only: `--dry-run`'s own purpose is exercising the orchestration
    # wiring on demand, any day, so it is deliberately left ungated.
    # Checked BEFORE the lock is ever acquired and before any broker
    # construction -- a non-trading day never touches either.
    if not args.dry_run:
        calendar = MarketCalendar()
        warning = calendar.verification_warning()
        if warning:
            print(f"WARNING: {warning}", file=sys.stderr)
        is_trading_day, reason = calendar.is_trading_day(date.fromisoformat(session_date))
        if not is_trading_day:
            print(f"not a trading day, skipping cleanly: {reason}")
            write_daily_heartbeat(args.heartbeat_path, DailySessionHeartbeat(
                session_date=session_date, runtime_status="NON_TRADING_DAY",
                last_observation_timestamp=None, last_intelligence_cycle_timestamp=None,
                rows_captured_today=0, last_error=None,
            ))
            return 0  # correctly doing nothing on a non-trading day IS success.

    if args.dry_run:
        capture_fn = _make_dry_run_capture_fn()
        intelligence_fn = _make_dry_run_intelligence_fn()
        completeness_fn = None  # dry-run touches no shared state -- no store to check, per this module's own docstring.
        lock = None  # dry-run touches no shared state -- no lock needed, per this module's own docstring.
    else:
        capture_fn = _make_real_capture_fn(session_date)
        intelligence_fn = _make_real_intelligence_fn(
            session_date, cycle_artifact_store_path=args.cycle_artifact_store_path,
            daily_artifact_store_path=args.daily_artifact_store_path,
        )
        completeness_fn = _make_real_completeness_fn(session_date)
        lock = ProcessLock(Path(args.lock_path))
        try:
            lock.acquire()
        except LockAcquisitionError as exc:
            print(f"refusing to start: {exc}", file=sys.stderr)
            return 1

    try:
        runtime = DailySessionRuntime(
            session_date=session_date, clock=lambda: datetime.now(timezone.utc),
            capture_fn=capture_fn, intelligence_fn=intelligence_fn, heartbeat_path=args.heartbeat_path,
            completeness_fn=completeness_fn,
        )
        loop = asyncio.get_running_loop()
        main_task = asyncio.current_task()
        _install_signal_handlers(loop, main_task)

        report = await runtime.run()
        print(report.to_dict())
        return 0 if report.final_stage == "SESSION_COMPLETE" else 1
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
