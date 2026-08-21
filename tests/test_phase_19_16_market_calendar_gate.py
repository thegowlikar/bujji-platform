"""Phase 19.16 -- Trading Calendar Gate.

Wires the already-existing, unmodified `bujji.market_calendar.MarketCalendar`
(Sprint 112 Deliverable 6) into `run_daily_intelligence_session.py`'s
`_main()`, closing the forensic-audit-found gap: the daily entrypoint
previously had zero trading-calendar awareness. Proves, against the
real deployed entrypoint (subprocess, not a re-implementation):

1. A weekend real (non-dry-run) invocation exits 0, writes an honest
   `NON_TRADING_DAY` heartbeat, and never creates a lock file.
2. `--dry-run` remains ungated -- unchanged behavior on a weekend.
3. `MarketCalendar` itself is untouched (imported, never edited).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from bujji.market_calendar import MarketCalendar

REPO_ROOT_MARKER = "run_daily_intelligence_session.py"


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    assert os.path.exists(os.path.join(root, REPO_ROOT_MARKER))
    return root


def test_market_calendar_correctly_identifies_a_real_weekend():
    """Sanity: the unmodified, already-existing class does what this
    fix relies on -- no behavior of MarketCalendar itself was changed."""
    import datetime as dt
    calendar = MarketCalendar()
    saturday = dt.date(2026, 8, 15)
    assert saturday.weekday() == 5
    is_trading, reason = calendar.is_trading_day(saturday)
    assert is_trading is False
    assert "weekend" in reason.lower()


def test_real_invocation_on_a_weekend_skips_cleanly_without_lock(tmp_path):
    """The exact, real, deployed entrypoint -- not a re-implementation --
    run as a subprocess against a real weekend date."""
    repo_root = _repo_root()
    heartbeat_path = str(tmp_path / "heartbeat.json")
    lock_path = str(tmp_path / "daily_intelligence.lock")

    result = subprocess.run(
        [sys.executable, "run_daily_intelligence_session.py", "--date", "2026-08-15",
         "--heartbeat-path", heartbeat_path, "--lock-path", lock_path],
        cwd=repo_root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "not a trading day" in result.stdout

    with open(heartbeat_path) as f:
        heartbeat = json.load(f)
    assert heartbeat["runtime_status"] == "NON_TRADING_DAY"
    assert heartbeat["session_date"] == "2026-08-15"
    assert heartbeat["last_error"] is None

    assert not os.path.exists(lock_path), "the lock must never be created for a non-trading day -- it was never acquired."


def test_real_invocation_on_a_weekday_does_not_short_circuit_on_calendar(tmp_path):
    """A real weekday must NOT be skipped by the calendar gate --
    confirmed by observing it proceed past the gate (it will still fail
    later, honestly, on FYERS credentials/broker access in this test
    environment -- but that failure must come from the real pipeline,
    never from `runtime_status=NON_TRADING_DAY`)."""
    repo_root = _repo_root()
    heartbeat_path = str(tmp_path / "heartbeat.json")
    lock_path = str(tmp_path / "daily_intelligence.lock")

    result = subprocess.run(
        [sys.executable, "run_daily_intelligence_session.py", "--date", "2026-08-14",  # a real Friday.
         "--heartbeat-path", heartbeat_path, "--lock-path", lock_path],
        cwd=repo_root, capture_output=True, text=True, timeout=60,
    )
    assert "not a trading day" not in result.stdout

    with open(heartbeat_path) as f:
        heartbeat = json.load(f)
    assert heartbeat["runtime_status"] != "NON_TRADING_DAY"


def test_dry_run_ignores_the_calendar_on_a_weekend(tmp_path):
    """`--dry-run`'s own established purpose (Phase 19.11 onward) is
    exercising the orchestration wiring on demand, any day -- the
    calendar gate must never intercept it."""
    repo_root = _repo_root()
    heartbeat_path = str(tmp_path / "heartbeat.json")
    lock_path = str(tmp_path / "daily_intelligence.lock")

    result = subprocess.run(
        [sys.executable, "run_daily_intelligence_session.py", "--date", "2026-08-15", "--dry-run",
         "--heartbeat-path", heartbeat_path, "--lock-path", lock_path],
        cwd=repo_root, capture_output=True, text=True, timeout=30,
    )
    assert "not a trading day" not in result.stdout

    with open(heartbeat_path) as f:
        heartbeat = json.load(f)
    assert heartbeat["runtime_status"] != "NON_TRADING_DAY"
    # dry-run's own real, established outcome (unchanged by this phase):
    # its stub capture/intelligence functions report themselves as
    # skipped, so the session is honestly FAILED, never fabricated success.
    assert heartbeat["runtime_status"] == "FAILED"


def test_verification_warning_printed_for_every_real_invocation(tmp_path):
    """`MarketCalendar.verification_warning()` (unmodified) must be
    surfaced, not silently swallowed -- the empty HOLIDAY_CALENDAR
    template is a real, disclosed limitation this fix does not resolve
    or hide."""
    repo_root = _repo_root()
    heartbeat_path = str(tmp_path / "heartbeat.json")
    lock_path = str(tmp_path / "daily_intelligence.lock")

    result = subprocess.run(
        [sys.executable, "run_daily_intelligence_session.py", "--date", "2026-08-15",
         "--heartbeat-path", heartbeat_path, "--lock-path", lock_path],
        cwd=repo_root, capture_output=True, text=True, timeout=30,
    )
    assert "holiday_calendar_verified=False" in result.stderr
