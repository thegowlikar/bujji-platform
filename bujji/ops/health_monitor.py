"""Operational Health Monitor (Sprint 4).

Observes RuntimeStatus and process-level signals; NEVER reads a trading
decision, NEVER writes one. Every threshold below is a documented,
first-pass constant -- consistent with every other calibration note in
this codebase -- not yet tuned against real incident history.
"""
from __future__ import annotations

import json
import logging
import os
import resource
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..core.clock import now_ist
from ..core.runtime_status import RuntimeStatus
from .models import HealthState, OpsSnapshot, worst

# First-pass thresholds -- documented, not calibrated. Same discipline as
# every MIC brain's threshold constants.
STALE_CANDLE_WARNING_SECONDS = 400     # A bit over one candle interval.
STALE_CANDLE_CRITICAL_SECONDS = 900    # Three candle intervals.
RESTART_WARNING_COUNT_PER_HOUR = 3
RESTART_CRITICAL_COUNT_PER_HOUR = 8
EXCEPTION_WARNING_COUNT_PER_HOUR = 5
EXCEPTION_CRITICAL_COUNT_PER_HOUR = 20
MEMORY_WARNING_KB = 500_000            # ~500MB -- this process runs at ~35-55MB normally.
MEMORY_CRITICAL_KB = 1_500_000
DISK_WARNING_FREE_PCT = 15.0
DISK_CRITICAL_FREE_PCT = 5.0


class ExceptionRateCounter(logging.Handler):
    """Rolling-window ERROR/CRITICAL counter, attached to the existing
    logger non-invasively -- does not alter any existing log line, only
    observes what's already being logged."""

    def __init__(self, window_seconds: float = 3600.0) -> None:
        super().__init__(level=logging.ERROR)
        self._window = window_seconds
        self._events: list[float] = []

    def emit(self, record: logging.LogRecord) -> None:
        self._events.append(time.monotonic())

    def count_in_window(self) -> int:
        cutoff = time.monotonic() - self._window
        self._events = [t for t in self._events if t >= cutoff]
        return len(self._events)


class HealthMonitor:
    """Stateful only in the ways an operations monitor must be (auth-expiry
    streak timing, restart-count persistence, exception rolling window).
    Never mutates RuntimeStatus's trading-relevant fields, only reads them."""

    def __init__(self, logger: logging.Logger, restart_count_file: Path,
                 decision_journal_path: Optional[Path] = None) -> None:
        self._log = logger
        self._restart_count_file = Path(restart_count_file)
        self._decision_journal_path = Path(decision_journal_path) if decision_journal_path else None
        self._process_start = time.monotonic()
        self._auth_expired_since: Optional[datetime] = None
        self._exception_counter = ExceptionRateCounter()
        logging.getLogger().addHandler(self._exception_counter)
        self._record_this_start()

    def _record_this_start(self) -> None:
        """Best-effort, append-only restart-timestamp log -- never raises,
        never blocks startup."""
        try:
            self._restart_count_file.parent.mkdir(parents=True, exist_ok=True)
            starts = []
            if self._restart_count_file.exists():
                starts = json.loads(self._restart_count_file.read_text())
            starts.append(now_ist().isoformat())
            starts = starts[-100:]  # Bounded, never grows unbounded.
            self._restart_count_file.write_text(json.dumps(starts))
        except Exception as exc:  # noqa: BLE001 - observational, must never block.
            self._log.error("ops_restart_count_write_failed err=%s", exc)

    def _restart_count_last_hour(self) -> int:
        try:
            starts = json.loads(self._restart_count_file.read_text())
        except Exception:
            return 0
        cutoff = now_ist().timestamp() - 3600
        return sum(1 for s in starts if datetime.fromisoformat(s).timestamp() >= cutoff)

    def observe(self, status: RuntimeStatus, journal_write_ok: bool,
                latest_decision_id: Optional[str], latest_trade_id: Optional[str]) -> OpsSnapshot:
        now = now_ist()
        reasons: list[str] = []
        states: list[HealthState] = []

        # Authentication streak tracking -- Sprint 3 Defect #1's fix.
        if status.auth_expired:
            if self._auth_expired_since is None:
                self._auth_expired_since = now
            duration = (now - self._auth_expired_since).total_seconds()
            states.append(HealthState.CRITICAL)
            reasons.append(f"auth_expired for {duration:.0f}s (since {self._auth_expired_since.isoformat()})")
        else:
            if self._auth_expired_since is not None:
                recovered_after = (now - self._auth_expired_since).total_seconds()
                self._log.warning("ops_auth_recovered duration_seconds=%.0f", recovered_after)
            self._auth_expired_since = None
        duration = ((now - self._auth_expired_since).total_seconds()
                   if self._auth_expired_since else None)

        # Market data freshness -- computed the same way the dashboard
        # does (RuntimeStatus itself only stores last_candle_ts, not a
        # precomputed age; candle_age_seconds is a serve-time derivation
        # there, and must be derived here too, not assumed to exist).
        candle_age = None
        if status.last_candle_ts:
            try:
                candle_age = (now - datetime.fromisoformat(status.last_candle_ts)).total_seconds()
            except (TypeError, ValueError):
                candle_age = None
        if candle_age is not None and candle_age >= STALE_CANDLE_CRITICAL_SECONDS:
            states.append(HealthState.CRITICAL); reasons.append(f"candle_age={candle_age:.0f}s")
        elif candle_age is not None and candle_age >= STALE_CANDLE_WARNING_SECONDS:
            states.append(HealthState.WARNING); reasons.append(f"candle_age={candle_age:.0f}s")

        # Restart frequency.
        restarts = self._restart_count_last_hour()
        if restarts >= RESTART_CRITICAL_COUNT_PER_HOUR:
            states.append(HealthState.CRITICAL); reasons.append(f"restarts_last_hour={restarts}")
        elif restarts >= RESTART_WARNING_COUNT_PER_HOUR:
            states.append(HealthState.WARNING); reasons.append(f"restarts_last_hour={restarts}")

        # Exception rate.
        exc_count = self._exception_counter.count_in_window()
        if exc_count >= EXCEPTION_CRITICAL_COUNT_PER_HOUR:
            states.append(HealthState.CRITICAL); reasons.append(f"exceptions_last_hour={exc_count}")
        elif exc_count >= EXCEPTION_WARNING_COUNT_PER_HOUR:
            states.append(HealthState.WARNING); reasons.append(f"exceptions_last_hour={exc_count}")

        # Memory (RSS, KB, via stdlib -- no new dependency).
        mem_kb = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if mem_kb >= MEMORY_CRITICAL_KB:
            states.append(HealthState.CRITICAL); reasons.append(f"memory_kb={mem_kb:.0f}")
        elif mem_kb >= MEMORY_WARNING_KB:
            states.append(HealthState.WARNING); reasons.append(f"memory_kb={mem_kb:.0f}")

        # Disk.
        disk = shutil.disk_usage(".")
        disk_free_pct = disk.free / disk.total * 100.0
        if disk_free_pct <= DISK_CRITICAL_FREE_PCT:
            states.append(HealthState.CRITICAL); reasons.append(f"disk_free_pct={disk_free_pct:.1f}")
        elif disk_free_pct <= DISK_WARNING_FREE_PCT:
            states.append(HealthState.WARNING); reasons.append(f"disk_free_pct={disk_free_pct:.1f}")

        # Journal / Decision Journal write health.
        decision_journal_ok = True
        if self._decision_journal_path is not None:
            decision_journal_ok = self._decision_journal_path.parent.exists()
        if not journal_write_ok:
            states.append(HealthState.CRITICAL); reasons.append("journal_write_failed")
        if not decision_journal_ok:
            states.append(HealthState.DEGRADED); reasons.append("decision_journal_path_unavailable")

        # WebSocket connectivity is DEGRADED, not CRITICAL, by design: the
        # candle-driven signal/exit path does not depend on it (only the
        # Tick Engine's faster MTM path does) -- losing it degrades
        # responsiveness, it does not blind the system to candles.
        if not status.ws_connected:
            states.append(HealthState.DEGRADED); reasons.append("ws_disconnected")

        health_state = worst(states) if states else HealthState.HEALTHY

        return OpsSnapshot(
            as_of=now, health_state=health_state, reasons=reasons,
            uptime_seconds=time.monotonic() - self._process_start,
            restart_count_last_hour=restarts,
            auth_expired=status.auth_expired,
            auth_expired_since=self._auth_expired_since.isoformat() if self._auth_expired_since else None,
            auth_expired_duration_seconds=duration,
            candle_age_seconds=candle_age,
            ws_connected=status.ws_connected,
            memory_rss_kb=mem_kb,
            disk_free_pct=disk_free_pct,
            exception_count_last_hour=exc_count,
            journal_write_ok=journal_write_ok,
            decision_journal_write_ok=decision_journal_ok,
            latest_decision_id=latest_decision_id,
            latest_trade_id=latest_trade_id,
        )
