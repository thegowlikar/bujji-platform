"""Operational Status Command -- Shadow Runtime, Phase 19.12/19.17.

Read-only: reads the already-real, already-written
`DailySessionHeartbeat` file (Phase 19.11's own `write_daily_heartbeat()`)
and the lock file's own liveness -- never a second, competing status
mechanism, never a new store. This module answers "what is the daily
runtime doing right now," nothing else.

Phase 19.17 addition: `stale` -- a naive, deliberately simple
time-since-last-write check against the heartbeat FILE's own mtime
(`os.path.getmtime`, not a new persistence mechanism -- the filesystem
already tracks this). This is intentionally NOT calendar-aware (it does
not know Saturday/Sunday are expected gaps) -- it answers "has anything
written to this file recently," nothing more. The calendar-aware,
authoritative answer to "was a specific expected trading day actually
missed" is `campaign_continuity.py` (Phase 19.17), which this module
does not duplicate.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .daily_session import DailySessionHeartbeat, read_daily_heartbeat

# Default staleness threshold: 24 hours. Deliberately configurable
# (per-call override) rather than hardcoded, since "appropriate" depends
# on context -- a human glancing at status mid-afternoon wants a tighter
# window than a script checking right after a weekend. Not weekend-aware
# by design -- see module docstring.
DEFAULT_STALE_THRESHOLD_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class OperationalStatus:
    lifecycle_state: str
    last_heartbeat_at: Optional[str]     # not the same as `last_observation_timestamp` -- this is WHEN the heartbeat file was last written, i.e. proof of liveness.
    session_date: Optional[str]
    last_successful_observation: Optional[str]
    last_failure: Optional[str]
    lock_held: bool
    stale: bool = False                  # Phase 19.17, additive default -- see module docstring.
    heartbeat_file_age_seconds: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "lifecycle_state": self.lifecycle_state, "last_heartbeat_at": self.last_heartbeat_at,
            "session_date": self.session_date, "last_successful_observation": self.last_successful_observation,
            "last_failure": self.last_failure, "lock_held": self.lock_held,
            "stale": self.stale, "heartbeat_file_age_seconds": self.heartbeat_file_age_seconds,
        }

    def render(self) -> str:
        lines = [
            "=== Bujji Daily Intelligence Session — Status ===",
            f"Lifecycle state           : {self.lifecycle_state}{'  [STALE]' if self.stale else ''}",
            f"Session date               : {self.session_date or 'unknown'}",
            f"Last heartbeat             : {self.last_heartbeat_at or 'never'}",
            f"Last successful observation: {self.last_successful_observation or 'none yet'}",
            f"Last failure                : {self.last_failure or 'none'}",
            f"Lock currently held         : {'yes' if self.lock_held else 'no'}",
        ]
        return "\n".join(lines)


def _lock_is_live(lock_path: str) -> bool:
    """A live `flock` cannot be inspected without attempting to acquire
    it -- reuses `ProcessLock` itself (Phase 19.12) rather than parsing
    the lock file's own bytes (the PID written into it is informational
    only, per `ProcessLock`'s own docstring; the real liveness signal is
    the OS-level lock, not the file's content)."""
    if not os.path.exists(lock_path):
        return False
    from bujji.core.process_lock import LockAcquisitionError, ProcessLock
    probe = ProcessLock(lock_path)
    try:
        probe.acquire()
    except LockAcquisitionError:
        return True  # a live process holds it.
    else:
        probe.release()
        return False  # acquirable -- no live holder.


def _heartbeat_file_age_seconds(heartbeat_path: str, *, now: datetime) -> Optional[float]:
    if not os.path.exists(heartbeat_path):
        return None
    mtime = datetime.fromtimestamp(os.path.getmtime(heartbeat_path), tz=timezone.utc)
    return max(0.0, (now - mtime).total_seconds())


def get_operational_status(
    heartbeat_path: str, *, lock_path: Optional[str] = None,
    now: Optional[datetime] = None, stale_threshold_seconds: float = DEFAULT_STALE_THRESHOLD_SECONDS,
) -> OperationalStatus:
    """`now` is caller-injected for deterministic testing (defaults to a
    real wall-clock read only when omitted, matching this project's own
    established "inject the clock" convention -- this is the one place
    in this module that legitimately needs `datetime.now()` at all,
    since staleness is inherently a real-time question)."""
    if now is None:
        now = datetime.now(timezone.utc)

    heartbeat: Optional[DailySessionHeartbeat] = read_daily_heartbeat(heartbeat_path)
    lock_held = _lock_is_live(lock_path) if lock_path else False
    age_seconds = _heartbeat_file_age_seconds(heartbeat_path, now=now)
    stale = age_seconds is not None and age_seconds > stale_threshold_seconds

    if heartbeat is None:
        return OperationalStatus(
            lifecycle_state="UNKNOWN", last_heartbeat_at=None, session_date=None,
            last_successful_observation=None, last_failure=None, lock_held=lock_held,
            stale=stale, heartbeat_file_age_seconds=age_seconds,
        )

    return OperationalStatus(
        lifecycle_state=heartbeat.runtime_status,
        last_heartbeat_at=heartbeat.last_intelligence_cycle_timestamp or heartbeat.last_observation_timestamp,
        session_date=heartbeat.session_date,
        last_successful_observation=heartbeat.last_observation_timestamp,
        last_failure=heartbeat.last_error, lock_held=lock_held,
        stale=stale, heartbeat_file_age_seconds=age_seconds,
    )
