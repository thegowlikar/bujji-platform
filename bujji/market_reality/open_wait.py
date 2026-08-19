"""Wait-for-open: capture from the FIRST sample of the session.

WHY (2026-08-19): the capture timers fired at 09:16/09:17 to clear the
scripts' own `now >= 09:15` gate with a safety margin -- the scar tissue of
the 09:00-era timer that fired before the gate and captured ZERO rows every
day while looking scheduled. The margin cost the opening minute: the most
volatile, least recoverable data of the day was never captured (operator
finding, 2026-08-19).

The fix is not to race the boundary again. Timers now fire at 09:14 and each
script WAITS for its own open instant instead of refusing: a start shortly
before the open holds until the gate opens, then samples immediately -- the
first observation lands within seconds of the first tick. A start far from
the open (a 05:00 manual run, a stray fire) still refuses exactly as before:
waiting hours would be a silent hang, and a refusal is honest.

PER-SCRIPT OPEN OFFSETS: the three capture processes would otherwise all
burst at 09:15:00.000 against the shared 10/s FYERS account ceiling (the
same collision the 09:22:30 trading offset exists to avoid). Each caller
passes its own small offset -- spot/vix/futures at +0s, option chain at +2s,
depth at +5s -- so first samples stay inside the opening seconds while the
startup bursts never stack.
"""
from __future__ import annotations

import datetime
from typing import Callable, Optional

MAX_WAIT_SECONDS_DEFAULT = 300.0  # a 09:14 fire waits <= ~60s; 5 min covers clock drift


def wait_until_open(
    *,
    now_fn: Callable[[], datetime.datetime],
    market_open: datetime.time,
    open_offset_seconds: float = 0.0,
    max_wait_seconds: float = MAX_WAIT_SECONDS_DEFAULT,
    sleep_fn: Callable[[float], None],
    logger,
) -> bool:
    """Block until today's open(+offset) when it is close; refuse when far.

    Returns True when the caller may proceed (either it is already at/after
    the open instant, or we waited and reached it). Returns False when the
    open is more than `max_wait_seconds` away -- the caller must refuse,
    never hang.  Never sleeps past the target: the last sleep is sized to
    land ON the instant, so the first capture happens within the opening
    second(s).
    """
    now = now_fn()
    target = now.replace(hour=market_open.hour, minute=market_open.minute,
                         second=market_open.second, microsecond=0)
    target += datetime.timedelta(seconds=open_offset_seconds)
    remaining = (target - now).total_seconds()
    if remaining <= 0:
        return True
    if remaining > max_wait_seconds:
        logger.warning(
            "Open is %.0fs away (> %.0fs) -- refusing to wait that long; this is a "
            "far-from-open start, not the 09:14 pre-open fire.", remaining, max_wait_seconds)
        return False
    logger.info("Pre-open start: waiting %.1fs for the open instant %s (first-sample capture).",
                remaining, target.time().isoformat())
    while True:
        remaining = (target - now_fn()).total_seconds()
        if remaining <= 0:
            return True
        sleep_fn(min(remaining, 1.0))
