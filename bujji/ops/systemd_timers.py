"""Ask systemd when things actually fire, instead of hardcoding it.

WHY THIS EXISTS. `scripts/promote_fyers_token.py` carried
FIRST_FIRE/LAST_FIRE constants describing the morning timer bracket. On
2026-08-19 the trading timer moved from 09:22:30 to 09:14 and the constant
went stale within a day -- still naming a unit that had moved, and pointing
at a time EARLIER than the real last token consumer (09:27:30), so the check
reported coverage it was not testing. This repo already carries a documented
history of exactly this failure: four disagreeing market-close constants.

`scripts/preflight_fyers_token.py` had already solved it by asking systemd
for each timer's real next elapse. That approach is lifted here so BOTH
scripts share one implementation rather than growing a second copy that can
drift from the first -- which would be the same bug one level up.

WHAT THIS ADDS BEYOND THE PREFLIGHT'S VERSION. The preflight deliberately
covers EVERY bujji timer, which is the conservative choice for a pre-open
alarm. Promote needs a narrower question -- "which fires actually need the
token?" -- because including, say, the 16:30 backup would make a token valid
until 09:30 report as insufficient and cry wolf every morning. So this module
also discovers WHICH units read the env file, by resolving each timer to its
service and inspecting the unit for that path. Schedule and consumers are
both discovered; neither is asserted.

NEVER FATAL. Every function degrades to None or an empty result when systemd
is absent (a laptop, a container, a test run). Callers fall back to their own
documented constants and say that they did -- an operator tool that refuses
to run off-box is a tool nobody runs.
"""
from __future__ import annotations

import datetime
import re
import subprocess
from typing import List, Optional, Tuple

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

TIMER_PREFIX = "bujji-"
DEFAULT_ENV_PATH = "/opt/bujji/.env"
_UNIT_DIRS = ("/etc/systemd/system", "/lib/systemd/system", "/usr/lib/systemd/system")


def _run(argv: List[str], timeout: int = 15) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout).stdout
    except Exception:  # noqa: BLE001 -- systemd absent is a normal case, not an error
        return ""


def host_timezone() -> datetime.tzinfo:
    """The tz systemd formats NextElapseUSecRealtime in (it prints local time)."""
    name = _run(["timedatectl", "show", "-p", "Timezone", "--value"], timeout=10).strip()
    if name:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(name)
        except Exception:  # noqa: BLE001
            pass
    return IST  # documented deployment tz; see docs/FYERS_TOKEN_LIFECYCLE.md


def bujji_timers(exclude: Tuple[str, ...] = ()) -> List[str]:
    """Every installed bujji-*.timer, sorted. Empty when systemd is absent."""
    names = []
    for line in _run(["systemctl", "list-units", "--type=timer", "--all",
                      "--plain", "--no-legend"]).splitlines():
        parts = line.split()
        if not parts:
            continue
        name = parts[0]
        if name in exclude or not name.startswith(TIMER_PREFIX):
            continue
        if name.endswith(".timer"):
            names.append(name)
    return sorted(names)


def next_elapse(timer: str, tz: datetime.tzinfo) -> Optional[datetime.datetime]:
    """Parse systemd's 'Wed 2026-08-19 09:16:00 IST' into an aware datetime."""
    raw = _run(["systemctl", "show", timer, "-p", "NextElapseUSecRealtime", "--value"],
               timeout=10).strip()
    if not raw or raw in ("n/a", "infinity"):
        return None
    body = raw.rsplit(" ", 1)[0]  # drop the trailing tz abbreviation
    for fmt in ("%a %Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(body, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def _unit_text(unit: str) -> str:
    """The unit's own file contents. `systemctl cat` first (it resolves
    drop-ins), falling back to the well-known directories."""
    text = _run(["systemctl", "cat", unit], timeout=10)
    if text.strip():
        return text
    import os

    for directory in _UNIT_DIRS:
        path = os.path.join(directory, unit)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    return handle.read()
            except OSError:
                continue
    return ""


def service_for(timer: str) -> str:
    """The service a timer activates. systemd's default is same-name .service,
    and `Unit=` overrides it -- honoured here rather than assumed."""
    match = re.search(r"^\s*Unit\s*=\s*(\S+)\s*$", _unit_text(timer), flags=re.M)
    if match:
        return match.group(1)
    return timer[: -len(".timer")] + ".service"


def consumes_env_file(timer: str, env_path: str = DEFAULT_ENV_PATH) -> bool:
    """Does this timer's service actually read the token file?

    Covers both wiring styles in use: `EnvironmentFile=/opt/bujji/.env` and an
    explicit `--fyers-env-file /opt/bujji/.env` on the ExecStart line.
    """
    return env_path in _unit_text(service_for(timer))


def token_fire_window(
    *,
    env_path: str = DEFAULT_ENV_PATH,
    exclude: Tuple[str, ...] = (),
) -> List[Tuple[str, datetime.datetime]]:
    """(timer, next_elapse) for every upcoming fire that needs the token.

    Sorted by time. Empty when systemd is unavailable or nothing qualifies --
    the caller decides what to do about that rather than being handed a
    fabricated bracket.
    """
    tz = host_timezone()
    fires = []
    for timer in bujji_timers(exclude=exclude):
        if not consumes_env_file(timer, env_path):
            continue
        when = next_elapse(timer, tz)
        if when is not None:
            fires.append((timer, when))
    return sorted(fires, key=lambda pair: pair[1])
