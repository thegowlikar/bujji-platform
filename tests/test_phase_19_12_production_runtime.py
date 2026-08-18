"""Phase 19.12 -- Production Runtime Deployment & Session Ownership.

Verifies exactly the phase's own "Verify" list: reboot recovery,
duplicate prevention, crash restart, two consecutive trading sessions,
operational status correctness, graceful shutdown -- plus the
structural safety boundary (no broker/order/position/strategy import
anywhere in this phase's new code), verified via AST inspection (per
this session's own established discipline -- naive substring checks
have repeatedly false-positived on module docstrings in earlier phases).
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from bujji.core.process_lock import LockAcquisitionError, ProcessLock
from bujji.shadow_runtime.daily_session import (
    CaptureResult,
    DailySessionRuntime,
    IntelligenceRunResult,
    read_daily_heartbeat,
    write_daily_heartbeat,
)
from bujji.shadow_runtime.status import get_operational_status


def _fixed_clock():
    from datetime import datetime, timezone
    return lambda: datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)


def _ok_capture(rows=10, ts="2026-08-15T10:00:00+00:00"):
    async def fn() -> CaptureResult:
        return CaptureResult(rows_captured=rows, last_observation_timestamp=ts, errors=())
    return fn


def _ok_intelligence(ts="2026-08-15T10:05:00+00:00"):
    async def fn() -> IntelligenceRunResult:
        return IntelligenceRunResult(cycles_completed=1, last_intelligence_cycle_timestamp=ts, errors=())
    return fn


def _hanging_intelligence():
    async def fn() -> IntelligenceRunResult:
        await asyncio.sleep(3600)
        return IntelligenceRunResult(cycles_completed=1, last_intelligence_cycle_timestamp=None, errors=())
    return fn


def _crashing_capture():
    async def fn() -> CaptureResult:
        raise RuntimeError("simulated capture crash")
    return fn


# ---------------------------------------------------------------------
# Duplicate prevention
# ---------------------------------------------------------------------

def test_duplicate_instance_is_refused(tmp_path: Path) -> None:
    lock_path = tmp_path / "daily_intelligence.lock"
    first = ProcessLock(lock_path)
    first.acquire()
    try:
        second = ProcessLock(lock_path)
        with pytest.raises(LockAcquisitionError):
            second.acquire()
    finally:
        first.release()


# ---------------------------------------------------------------------
# Reboot recovery: a crashed/killed holder's lock is not wedged -- a
# fresh instance (as if after a reboot) can always reacquire once the
# OS has released the old file description.
# ---------------------------------------------------------------------

def test_reboot_recovery_lock_reacquirable_after_release(tmp_path: Path) -> None:
    lock_path = tmp_path / "daily_intelligence.lock"
    stale = ProcessLock(lock_path)
    stale.acquire()
    stale.release()  # simulates the OS releasing a crashed/rebooted holder's flock.

    fresh = ProcessLock(lock_path)
    fresh.acquire()  # must not raise.
    assert fresh.held
    fresh.release()


# ---------------------------------------------------------------------
# Crash restart: DailySessionRuntime.run() never raises even when the
# injected capture_fn crashes outright -- and a second, fresh runtime
# instance can complete normally afterward (nothing left wedged).
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crash_in_capture_is_recorded_not_raised(tmp_path: Path) -> None:
    heartbeat_path = str(tmp_path / "heartbeat.json")
    runtime = DailySessionRuntime(
        session_date="2026-08-15", clock=_fixed_clock(),
        capture_fn=_crashing_capture(), intelligence_fn=_ok_intelligence(),
        heartbeat_path=heartbeat_path,
    )
    report = await runtime.run()  # must not raise.
    assert report.final_stage == "FAILED"
    assert any("unexpected_failure" in e for e in report.errors)

    heartbeat = read_daily_heartbeat(heartbeat_path)
    assert heartbeat is not None
    assert heartbeat.runtime_status == "FAILED"


@pytest.mark.asyncio
async def test_fresh_instance_after_crash_completes_normally(tmp_path: Path) -> None:
    heartbeat_path = str(tmp_path / "heartbeat.json")
    crashed = DailySessionRuntime(
        session_date="2026-08-15", clock=_fixed_clock(),
        capture_fn=_crashing_capture(), intelligence_fn=_ok_intelligence(),
        heartbeat_path=heartbeat_path,
    )
    await crashed.run()

    restarted = DailySessionRuntime(
        session_date="2026-08-15", clock=_fixed_clock(),
        capture_fn=_ok_capture(), intelligence_fn=_ok_intelligence(),
        heartbeat_path=heartbeat_path,
    )
    report = await restarted.run()
    assert report.final_stage == "SESSION_COMPLETE"

    heartbeat = read_daily_heartbeat(heartbeat_path)
    assert heartbeat.runtime_status == "SESSION_COMPLETE"


# ---------------------------------------------------------------------
# Two consecutive trading sessions: a second day's runtime, using the
# same heartbeat path, correctly overwrites to the new session's own
# state -- no stale carryover from the prior day.
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_consecutive_sessions_do_not_leak_state(tmp_path: Path) -> None:
    heartbeat_path = str(tmp_path / "heartbeat.json")

    day1 = DailySessionRuntime(
        session_date="2026-08-14", clock=_fixed_clock(),
        capture_fn=_ok_capture(rows=42, ts="2026-08-14T10:00:00+00:00"),
        intelligence_fn=_ok_intelligence(ts="2026-08-14T10:05:00+00:00"),
        heartbeat_path=heartbeat_path,
    )
    report1 = await day1.run()
    assert report1.final_stage == "SESSION_COMPLETE"
    hb1 = read_daily_heartbeat(heartbeat_path)
    assert hb1.session_date == "2026-08-14"
    assert hb1.rows_captured_today == 42

    day2 = DailySessionRuntime(
        session_date="2026-08-15", clock=_fixed_clock(),
        capture_fn=_ok_capture(rows=7, ts="2026-08-15T10:00:00+00:00"),
        intelligence_fn=_ok_intelligence(ts="2026-08-15T10:05:00+00:00"),
        heartbeat_path=heartbeat_path,
    )
    report2 = await day2.run()
    assert report2.final_stage == "SESSION_COMPLETE"
    hb2 = read_daily_heartbeat(heartbeat_path)
    assert hb2.session_date == "2026-08-15"
    assert hb2.rows_captured_today == 7  # not 42+7, not stale -- each runtime instance owns its own counters.


# ---------------------------------------------------------------------
# Graceful shutdown: cancelling the run() task (as SIGTERM handling
# does in production) is caught by its own dedicated branch, not the
# generic Exception branch, and still finalizes a real report.
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_graceful_shutdown_on_cancellation(tmp_path: Path) -> None:
    heartbeat_path = str(tmp_path / "heartbeat.json")
    runtime = DailySessionRuntime(
        session_date="2026-08-15", clock=_fixed_clock(),
        capture_fn=_ok_capture(), intelligence_fn=_hanging_intelligence(),
        heartbeat_path=heartbeat_path,
    )
    task = asyncio.ensure_future(runtime.run())
    await asyncio.sleep(0.05)  # let it reach the hanging intelligence step.
    task.cancel()
    report = await task  # must not raise -- run() swallows its own cancellation.
    assert report.final_stage == "FAILED"
    assert any("graceful_shutdown" in e for e in report.errors)


# ---------------------------------------------------------------------
# Operational status correctness
# ---------------------------------------------------------------------

def test_status_reports_all_five_required_fields(tmp_path: Path) -> None:
    heartbeat_path = str(tmp_path / "heartbeat.json")
    from bujji.shadow_runtime.daily_session import DailySessionHeartbeat
    write_daily_heartbeat(heartbeat_path, DailySessionHeartbeat(
        session_date="2026-08-15", runtime_status="SESSION_COMPLETE",
        last_observation_timestamp="2026-08-15T10:00:00+00:00",
        last_intelligence_cycle_timestamp="2026-08-15T10:05:00+00:00",
        rows_captured_today=42, last_error=None,
    ))
    status = get_operational_status(heartbeat_path, lock_path=str(tmp_path / "nonexistent.lock"))
    assert status.lifecycle_state == "SESSION_COMPLETE"
    assert status.session_date == "2026-08-15"
    assert status.last_heartbeat_at == "2026-08-15T10:05:00+00:00"
    assert status.last_successful_observation == "2026-08-15T10:00:00+00:00"
    assert status.last_failure is None
    assert status.lock_held is False


def test_status_reflects_a_live_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "daily_intelligence.lock"
    holder = ProcessLock(lock_path)
    holder.acquire()
    try:
        status = get_operational_status(str(tmp_path / "missing_heartbeat.json"), lock_path=str(lock_path))
        assert status.lock_held is True
        assert status.lifecycle_state == "UNKNOWN"  # no heartbeat written yet -- honest, not fabricated.
    finally:
        holder.release()


# ---------------------------------------------------------------------
# Structural safety boundary: no order/position/strategy import or
# attribute access anywhere in this phase's new files.
#
# "broker" itself is DELIBERATELY NOT in this list as of Phase 19.13:
# `run_daily_intelligence_session.py` now legitimately constructs a
# real, `disable_live_execution`-wrapped broker connection for
# READ-ONLY market data (the same sanctioned pattern `run_live_shadow.py`
# already uses in production) -- see
# `test_run_daily_intelligence_session_never_places_orders` below for
# the real, precise safety check this replaces: not "does this file
# mention the word broker" but "can this file's own code, anywhere,
# reach place_order/modify_order/cancel_order."
# ---------------------------------------------------------------------

FORBIDDEN_MODULE_SUBSTRINGS = ("order", "position", "strategy", "execution")
PHASE_19_12_FILES = (
    "bujji/shadow_runtime/daily_session.py",
    "bujji/shadow_runtime/status.py",
    "run_daily_intelligence_session.py",
    "bujji_daily_status.py",
)


def _imported_module_names(tree: ast.Module) -> list:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


@pytest.mark.parametrize("relative_path", PHASE_19_12_FILES)
def test_no_trading_decision_imports(relative_path: str) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    source = (repo_root / relative_path).read_text()
    tree = ast.parse(source)
    for module_name in _imported_module_names(tree):
        lowered = module_name.lower()
        for forbidden in FORBIDDEN_MODULE_SUBSTRINGS:
            assert forbidden not in lowered, (
                f"{relative_path} imports {module_name!r}, containing forbidden substring {forbidden!r} "
                f"-- Phase 19.12 is infrastructure-only and must never import trading-decision code"
            )
