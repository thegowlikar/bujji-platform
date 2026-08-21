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
from promote_fyers_token import IST, TARGET, access_token_expiry, keyed_lines  # noqa: E402

DEFAULT_VERDICT_PATH = REPO_ROOT / "data" / "token_preflight_verdict.json"
DEFAULT_HORIZON_HOURS = 12
_TIMER_PREFIX = "bujji-"

# This unit's own timer is NOT a token consumer. It USES the token now -- one
# `profile` call to ask FYERS whether the token is honoured (see
# live_token_check; the old claim that this "never calls the broker" stopped
# being true on 2026-08-21) -- but it consumes nothing a dead token would
# lose: no market data, no order, no observation. Counting itself would report
# a self-inflicted failure and inflate every verdict.
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


LIVE_VALID = "VALID"
LIVE_INVALID = "INVALID"
LIVE_UNKNOWN = "UNKNOWN"


def live_token_check(env_file: Path, timeout_seconds: float = 15.0):
    """Ask FYERS whether the token actually works. (status, detail).

    WHY THE `exp` CLAIM IS NOT ENOUGH -- measured 2026-08-21 17:20. The JWT
    said `exp` = 2026-08-22T06:00, this pre-flight said OK, and FYERS said
    `code=-15, "Please provide valid token"`. Both were true: `exp` is when a
    token WOULD expire, not whether it is still honoured. FYERS revokes
    outstanding tokens on a new interactive login, so a dashboard visit is
    enough to kill one hours before its `exp`.

    That gap matters more now than it used to, because six units are gated
    behind this verdict. An `exp`-only OK opens the gate for a token the
    venue has already thrown away -- exactly the lost day the gate exists to
    prevent.

    `profile` is the cheapest authenticated call FYERS offers: it touches no
    market data, moves no order, and the codebase already uses it for token
    validation (bujji/broker/fyers.py). One call at 08:45 plus one per gated
    unit is negligible against the 10/s per-account ceiling.

    UNKNOWN IS NOT INVALID, and this is the load-bearing distinction. A
    network blip, a DNS failure or a timeout means "I could not ask" -- and
    this codebase is emphatic that a read which fails must never become a
    negative finding (see _broker_reports_flat: "I could not ask" must never
    become "there is nothing there"). Reporting UNKNOWN as INVALID would
    block every unit whenever FYERS is briefly unreachable, converting a
    transient into a lost day, which is the very failure being fixed.
    """
    import asyncio
    import logging

    sys.path.insert(0, str(REPO_ROOT))
    try:
        from bujji.broker.errors import AuthenticationError
        from bujji.broker.fyers import FyersBroker
        from bujji.broker.guard import disable_live_execution
        from bujji.core.config import BrokerConfig
    except Exception as exc:  # noqa: BLE001
        return LIVE_UNKNOWN, "could not import the broker: %s: %s" % (type(exc).__name__, exc)

    try:
        # Same parser promote_fyers_token uses -- one place understands this
        # file's format, exactly as this module already does for the JWT.
        values = {k: v.split("=", 1)[1].strip().strip("\"'")
                  for k, v in keyed_lines(env_file).items()}
    except Exception as exc:  # noqa: BLE001
        return LIVE_UNKNOWN, "could not read %s: %s" % (env_file, exc)

    if not values.get("FYERS_APP_ID") or not values.get("FYERS_ACCESS_TOKEN"):
        return LIVE_INVALID, "FYERS_APP_ID / FYERS_ACCESS_TOKEN missing from %s" % env_file

    log = logging.getLogger("preflight-live")
    # The fyers SDK writes its own log file relative to CWD and raises if the
    # directory is missing or unwritable -- which is how this check first
    # reported UNKNOWN twice from a worktree. Run it in a scratch directory
    # instead: an infrastructure detail must not masquerade as a verdict on
    # the token, and this check has to work from wherever it is invoked.
    import os, shutil, tempfile
    scratch = tempfile.mkdtemp(prefix="preflight-fyers-")
    previous_cwd = os.getcwd()
    try:
        os.makedirs(os.path.join(scratch, "logs"), exist_ok=True)
        os.chdir(scratch)
        broker = disable_live_execution(FyersBroker(
            BrokerConfig(name="fyers", app_id=values["FYERS_APP_ID"],
                         access_token=values["FYERS_ACCESS_TOKEN"]), log))

        async def _probe():
            await broker.connect()
            return await broker._call("profile")

        data = asyncio.run(asyncio.wait_for(_probe(), timeout=timeout_seconds)) or {}
    except AuthenticationError as exc:
        # THE TOKEN IS DEAD, AND THIS IS A VERDICT, NOT AN UNKNOWN.
        #
        # FyersBroker RAISES rather than returning the error dict for exactly
        # the auth cases -- its classifier's docstring is explicit that it
        # "never raises for anything else" (bad symbol, margin, etc. pass
        # through). So this exception IS the venue saying no, and reusing that
        # judgement beats re-deriving a list of auth codes here.
        #
        # A negative control caught this: a deliberately corrupted token first
        # reported UNKNOWN, because a bare `except Exception` swallowed the
        # AuthenticationError into "could not ask". A dead token reading as
        # "couldn't ask" is the precise failure this whole check exists to
        # remove, so it must be caught BEFORE the generic handler below.
        return LIVE_INVALID, str(exc)[:140]
    except Exception as exc:  # noqa: BLE001 -- could not ask; never a verdict on the token
        return LIVE_UNKNOWN, "%s: %s" % (type(exc).__name__, str(exc)[:120])

    finally:
        try:
            os.chdir(previous_cwd)
            shutil.rmtree(scratch, ignore_errors=True)
        except Exception:  # noqa: BLE001 -- cleanup must never become the verdict
            pass

    code = data.get("code")
    if code == 200 or str(data.get("s", "")).lower() == "ok":
        return LIVE_VALID, "FYERS accepted the token (profile code=%s)" % code
    # FYERS answered, and its answer was no. That IS a verdict.
    return LIVE_INVALID, "FYERS rejected the token: code=%s message=%r" % (
        code, str(data.get("message"))[:100])


def assess(expiry, fires: list, now: datetime.datetime,
           live=(None, "")) -> dict:
    """Pure verdict function -- no I/O, so it is directly testable.

    `live` is the ALREADY-PERFORMED result of live_token_check, passed in
    rather than called, so this stays a pure function of its inputs and a
    test can drive every combination without a network."""
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
    live_status, live_detail = live

    # A token the venue has already revoked fails, whatever `exp` claims.
    if live_status == LIVE_INVALID:
        status = "FAIL"
        reason = "FYERS rejected the token (%s)" % live_detail

    verdict = {
        "checked_at": now.isoformat(),
        "token_expires_at": expiry.isoformat() if expiry else None,
        "status": status,
        "reason": reason,
        "fires": checked,
        "live_check": live_status,
        "live_detail": live_detail,
    }
    # UNKNOWN never downgrades an OK -- it is recorded, and said out loud, so
    # the operator knows the verdict rests on `exp` alone this run.
    if live_status == LIVE_UNKNOWN and status == "OK":
        verdict["reason"] = reason + " (NOT confirmed with FYERS: %s)" % live_detail
    return verdict


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", default=str(TARGET),
                    help="env file the units read (default: %(default)s)")
    ap.add_argument("--verdict-path", default=str(DEFAULT_VERDICT_PATH),
                    help="where to write the machine-readable verdict")
    ap.add_argument("--skip-live", action="store_true",
                    help="do not ask FYERS; the verdict then rests on the JWT exp "
                         "claim alone and records live_check=UNKNOWN")
    ap.add_argument("--live-timeout", type=float, default=15.0,
                    help="seconds to wait for FYERS before recording UNKNOWN")
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

    # ASK FYERS, unless explicitly told not to. `--skip-live` exists so the
    # verdict function stays exercisable offline (tests, a boxed host) -- it
    # is NOT a way to make a failing token pass, because a skipped check
    # records itself as UNKNOWN rather than as VALID.
    live = (LIVE_UNKNOWN, "live check skipped (--skip-live)") if args.skip_live \
        else live_token_check(Path(args.env_file), timeout_seconds=args.live_timeout)
    verdict = assess(access_token_expiry(Path(args.env_file)), fires, now, live=live)

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
    print("  live check: %s -- %s" % (verdict["live_check"], verdict["live_detail"]))
    if verdict["live_check"] == LIVE_UNKNOWN:
        print("  NOTE: FYERS was not reached, so this verdict rests on the token's own")
        print("        `exp` claim alone. A token revoked early (a dashboard login does")
        print("        it) would still read as OK. Re-run to confirm.")
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
