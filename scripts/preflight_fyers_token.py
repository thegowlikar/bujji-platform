#!/usr/bin/env python3
"""Pre-open check: will the FYERS access token survive today's timer fires?

WHY THIS EXISTS
---------------
The FYERS access token dies at a fixed 06:00 IST cutover every day, and the
capture/trading timers fire between 09:10 and 09:27:30. That leaves a
mandatory human refresh in a ~3h10m window every trading morning (the refresh
API is SEBI-disabled -- see docs/FYERS_TOKEN_LIFECYCLE.md). Nothing checked
that the refresh had actually happened, so a missed morning produced a silent,
permanent loss of that day's observations: on 2026-08-18 the 09:16 session
failed at 09:16:00 and was not noticed until 18:00 IST, one full session later.

This script does not refresh anything and cannot -- it only answers, BEFORE the
open, whether the token in /opt/bujji/.env outlives every fire still ahead
today, and fails loudly if it does not.

FIRE TIMES ARE DISCOVERED, NOT HARDCODED
----------------------------------------
`scripts/promote_fyers_token.py` carries its own FIRST_FIRE/LAST_FIRE
constants. Constants drift away from the units they describe (this repo
already has four disagreeing market-close constants). This script instead asks
systemd for each `bujji-*.timer`'s actual next elapse, so moving a timer moves
this check automatically and a newly installed bujji timer is covered with no
edit here.

EXIT CODES
    0  every upcoming fire is covered by the current token (or none are due)
    1  at least one upcoming fire runs with a dead token, or the token is
       unreadable -- systemd marks the unit failed and the verdict file says why
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Reuse the existing decoder rather than reimplementing JWT parsing: one place
# understands the token format, and it is already proven against the real file.
from promote_fyers_token import IST, TARGET, access_token_expiry  # noqa: E402

DEFAULT_VERDICT_PATH = REPO_ROOT / "data" / "token_preflight_verdict.json"
DEFAULT_HORIZON_HOURS = 12
_TIMER_PREFIX = "bujji-"

# This unit's own timer is NOT a token consumer: the check decodes a local
# JWT and never calls the broker, so it runs fine with a dead token. Counting
# it would report a self-inflicted failure and inflate every verdict.
SELF_TIMER = "bujji-token-preflight.timer"


def host_timezone() -> datetime.tzinfo:
    """The tz systemd formats NextElapseUSecRealtime in (it prints local time)."""
    try:
        name = subprocess.run(
            ["timedatectl", "show", "-p", "Timezone", "--value"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if name:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
    except Exception:
        pass
    return IST  # documented deployment tz; see docs/FYERS_TOKEN_LIFECYCLE.md


def active_bujji_timers() -> list:
    out = subprocess.run(
        ["systemctl", "list-units", "--type=timer", "--all", "--plain", "--no-legend"],
        capture_output=True, text=True, timeout=15,
    ).stdout
    names = []
    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue
        name = parts[0]
        if name == SELF_TIMER:
            continue
        if name.startswith(_TIMER_PREFIX) and name.endswith(".timer"):
            names.append(name)
    return sorted(names)


def next_elapse(timer: str, tz: datetime.tzinfo):
    """Parse systemd's 'Wed 2026-08-19 09:16:00 IST' into an aware datetime."""
    raw = subprocess.run(
        ["systemctl", "show", timer, "-p", "NextElapseUSecRealtime", "--value"],
        capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    if not raw or raw in ("n/a", "infinity"):
        return None
    body = raw.rsplit(" ", 1)[0]  # drop the trailing tz abbreviation
    for fmt in ("%a %Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(body, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def assess(expiry, fires: list, now: datetime.datetime) -> dict:
    """Pure verdict function -- no I/O, so it is directly testable."""
    checked = []
    for timer, fire in fires:
        checked.append({
            "timer": timer,
            "fires_at": fire.isoformat(),
            "covered": bool(expiry is not None and expiry > fire),
        })
    if expiry is None:
        status = "FAIL"
        reason = "FYERS_ACCESS_TOKEN missing or undecodable in %s" % TARGET
    elif not checked:
        status = "OK"
        reason = "no bujji timer fires due within the horizon"
    elif all(c["covered"] for c in checked):
        status = "OK"
        reason = "token outlives every upcoming fire"
    else:
        uncovered = [c["timer"] for c in checked if not c["covered"]]
        status = "FAIL"
        reason = "token expires before: %s" % ", ".join(uncovered)
    return {
        "checked_at": now.isoformat(),
        "token_expires_at": expiry.isoformat() if expiry else None,
        "status": status,
        "reason": reason,
        "fires": checked,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", default=str(TARGET),
                    help="env file the units read (default: %(default)s)")
    ap.add_argument("--verdict-path", default=str(DEFAULT_VERDICT_PATH),
                    help="where to write the machine-readable verdict")
    ap.add_argument("--horizon-hours", type=float, default=DEFAULT_HORIZON_HOURS,
                    help="only consider fires this far ahead (default: %(default)s)")
    args = ap.parse_args()

    tz = host_timezone()
    now = datetime.datetime.now(IST)
    horizon = now + datetime.timedelta(hours=args.horizon_hours)

    fires = []
    for timer in active_bujji_timers():
        when = next_elapse(timer, tz)
        if when is not None and now < when <= horizon:
            fires.append((timer, when))
    fires.sort(key=lambda p: p[1])

    verdict = assess(access_token_expiry(Path(args.env_file)), fires, now)

    vp = Path(args.verdict_path)
    vp.parent.mkdir(parents=True, exist_ok=True)
    vp.write_text(json.dumps(verdict, indent=2) + "\n")

    exp = verdict["token_expires_at"]
    print("FYERS token pre-flight  --  %s" % now.strftime("%Y-%m-%d %H:%M:%S %Z"))
    print("  token expires: %s" % (exp or "UNREADABLE"))
    if not verdict["fires"]:
        print("  no bujji timer fires within %sh" % args.horizon_hours)
    for c in verdict["fires"]:
        mark = "ok  " if c["covered"] else "DEAD"
        print("  [%s] %-38s fires %s" % (mark, c["timer"], c["fires_at"][11:19]))
    print("  verdict: %s -- %s" % (verdict["status"], verdict["reason"]))

    if verdict["status"] != "OK":
        print()
        print("  ACTION REQUIRED -- refresh the FYERS token, then promote it:")
        print("    cd /opt/bujji/app && /opt/bujji/.venv/bin/python \\")
        print("        scripts/promote_fyers_token.py")
        print("  Nothing here can do this for you: the FYERS refresh API is")
        print("  SEBI-disabled, so a human must re-authenticate. Until then the")
        print("  runs above will authenticate against a dead token and that")
        print("  day's observations are permanently lost.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
