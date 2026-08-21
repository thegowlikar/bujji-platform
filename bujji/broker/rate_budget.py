"""CP-D: one FYERS account, one rate budget, shared across every process.

THE PROBLEM, measured rather than assumed. FYERS enforces its request-rate
ceiling per ACCOUNT. The pacer in bujji/broker/fyers.py is module level --
per interpreter -- so it holds one PROCESS to ~8.3 calls/s and knows nothing
about any other. Bujji now runs three units that wake together at 09:14
(spot/VIX capture, option chain, depth poller) plus the trading session, all
against the same credentials. Four processes each pacing to 8.3/s present
the account with up to ~33/s against a documented 10/s ceiling.

Nothing detected this because a refusal is retried and mostly succeeds. It
showed up instead as an architectural workaround: the trading unit's fire
time was offset by half a cycle purely to keep its bursts from landing on
the capture units' beat. Schedule separation was the only cross-process rate
control that existed. That is a scheduling hack standing in for a resource
budget, and it cost the opening minutes of the market until the gate work
removed that dependency.

THE MECHANISM. A single small file holds the timestamp of the next allowed
call. Every process reserves its slot under an exclusive advisory lock
(fcntl.flock), writes the next slot back, releases the lock, and only THEN
sleeps. Reserving under the lock and waiting outside it is what makes
concurrent callers queue behind one another instead of all waking against
the same timestamp and bursting together -- the same discipline the
in-process pacer already uses, lifted to the host.

WHAT THIS COSTS, stated plainly: throughput is now shared, not multiplied. An
82-contract chain sweep that used to race its siblings will take about ten
seconds of wall clock while other processes interleave. That is not a
regression -- it is the account's real capacity, which was always 10/s and
was previously being exceeded and papered over with retries.

CLOCK. time.monotonic() maps to CLOCK_MONOTONIC on Linux, which is
system-wide, so two processes on this host compare slots meaningfully. It
cannot be compared across a reboot, and a stored value from before one would
be far in the future or the past -- both are rejected as implausible and the
budget restarts from now, rather than blocking every call for the length of
the previous uptime.

FAIL-OPEN, ON PURPOSE, AND LOUDLY. If the budget file cannot be opened or
locked, this returns without waiting and reports the failure. The in-process
pacer is still in place behind it, so the fallback is the old behaviour
rather than no pacing at all -- and a rate ceiling is a throughput
protection, not a safety guard: refusing to trade because a lock file is
unwritable would turn a throughput problem into an outage.
"""
from __future__ import annotations

import errno
import os
import time
from dataclasses import dataclass
from typing import Callable, Optional

try:  # pragma: no cover -- POSIX only; the fallback path is exercised by tests
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

DEFAULT_BUDGET_PATH = "/opt/bujji/app/data/fyers_rate_budget"

# The whole HOST's budget, not one process's. Under the documented 10/s
# account ceiling with headroom for the retry path's own calls.
DEFAULT_MIN_INTERVAL_SECONDS = 0.12

# A stored slot further ahead than this is not a real reservation -- it is a
# corrupt file or a value from before a reboot. Waiting on it would stall
# every Bujji process for as long as the previous uptime.
_MAX_PLAUSIBLE_RESERVATION_SECONDS = 60.0


@dataclass(frozen=True)
class SlotOutcome:
    """What actually happened, so a caller can report coverage instead of
    assuming the budget applied."""

    waited_seconds: float
    shared: bool           # False means the fallback ran: this call was NOT budgeted
    reason: Optional[str] = None


class CrossProcessRateBudget:
    """Host-wide pacing for one broker account."""

    def __init__(
        self,
        path: str = DEFAULT_BUDGET_PATH,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._path = path
        self._interval = max(float(min_interval_seconds), 0.0)
        self._clock = clock
        self._sleep = sleep

    @property
    def path(self) -> str:
        return self._path

    def reserve(self) -> SlotOutcome:
        """Reserve this process's next call slot and wait for it."""
        if fcntl is None:
            return SlotOutcome(0.0, shared=False, reason="fcntl unavailable on this platform")
        try:
            slot = self._reserve_locked()
        except OSError as exc:
            return SlotOutcome(0.0, shared=False, reason=f"{errno.errorcode.get(exc.errno, exc.errno)}: {exc}")

        delay = slot - self._clock()
        if delay > 0:
            self._sleep(delay)
            return SlotOutcome(delay, shared=True)
        return SlotOutcome(0.0, shared=True)

    def _reserve_locked(self) -> float:
        """Read, advance and write the shared slot under an exclusive lock.

        The lock is held for a file read and a write of at most 32 bytes --
        deliberately no sleeping inside it, or every process would serialise
        on the slowest one's wait rather than on the account's rate.
        """
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o664)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                now = self._clock()
                stored = self._read_stored(fd)
                slot = now if stored is None else max(now, stored)
                os.lseek(fd, 0, os.SEEK_SET)
                os.ftruncate(fd, 0)
                os.write(fd, f"{slot + self._interval:.6f}".encode("ascii"))
                return slot
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _read_stored(self, fd: int) -> Optional[float]:
        """The stored slot, or None when it is absent, unreadable, or not
        plausible. An implausible value is DISCARDED rather than trusted:
        the failure mode of trusting it is every Bujji process blocking for
        the length of the last uptime, which looks exactly like a hang."""
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 64).decode("ascii", errors="replace").strip()
        if not raw:
            return None
        try:
            stored = float(raw)
        except ValueError:
            return None
        now = self._clock()
        if stored > now + _MAX_PLAUSIBLE_RESERVATION_SECONDS:
            return None
        if stored < now - _MAX_PLAUSIBLE_RESERVATION_SECONDS:
            return None  # stale (pre-reboot); no reason to honour it
        return stored
