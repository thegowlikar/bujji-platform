"""Explicit timezone & clock-drift management (D1/D2).

All market-time comparisons must be pinned to Asia/Kolkata regardless of the
host's configured timezone — a host running in UTC (the default on many cloud
VMs) must not silently reinterpret config times like ``09:15``/``15:15`` as
UTC. This module is the single place "now" is obtained for anything
session-window-related; nothing else in the codebase should call
``datetime.now()`` directly for that purpose.

Clock-drift detection compares wall-clock elapsed time against the monotonic
clock between successive checks: if they diverge beyond a threshold, the OS
clock was adjusted (NTP correction, suspend/resume, manual change) during the
run. This requires no network dependency and catches *in-session* drift/jumps.

Scope note: this does NOT validate that the clock was already correct before
the process started (that would need an external trusted time source, e.g.
broker server time or NTP) — that remains a further improvement, out of scope
for this pass. What is implemented: (1) all session-window comparisons are
explicitly IST, independent of host TZ, and (2) a runtime jump in the wall
clock during a live session is detected and surfaced distinctly.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    """Current time, explicitly in Asia/Kolkata, independent of host TZ."""
    return datetime.now(IST)


def epoch_to_ist(epoch_seconds: float) -> datetime:
    """Convert a broker epoch timestamp to an explicit IST datetime.

    Never use the naive ``datetime.fromtimestamp(epoch)`` for broker data — it
    interprets the epoch using the host's local timezone setting, which is
    wrong the moment the host isn't configured for IST.
    """
    return datetime.fromtimestamp(epoch_seconds, tz=IST)


@dataclass(frozen=True)
class ClockDriftResult:
    drifted: bool
    delta_seconds: float
    detail: str


class ClockGuard:
    """Detects wall-clock jumps during a running process (D1).

    Call :meth:`check` periodically (e.g. once per loop boundary). The first
    call always reports no drift — there is nothing yet to compare against.
    A detected drift is a **point-in-time** signal for the interval since the
    last check, not a persistent flag; it naturally "clears" on the next
    check once the clock is no longer actively jumping.
    """

    def __init__(self, max_drift_seconds: float = 5.0) -> None:
        self._max_drift = max_drift_seconds
        self._last_wall: Optional[float] = None
        self._last_mono: Optional[float] = None

    def check(self) -> ClockDriftResult:
        wall = time.time()
        mono = time.monotonic()
        if self._last_wall is None or self._last_mono is None:
            self._last_wall, self._last_mono = wall, mono
            return ClockDriftResult(False, 0.0, "baseline_established")

        wall_elapsed = wall - self._last_wall
        mono_elapsed = mono - self._last_mono
        delta = wall_elapsed - mono_elapsed
        self._last_wall, self._last_mono = wall, mono

        if abs(delta) > self._max_drift:
            return ClockDriftResult(
                True, delta,
                f"wall clock moved {delta:+.2f}s relative to monotonic time "
                f"since the last check (threshold {self._max_drift}s) — "
                f"NTP correction, suspend/resume, or manual clock change",
            )
        return ClockDriftResult(False, delta, "within_threshold")
