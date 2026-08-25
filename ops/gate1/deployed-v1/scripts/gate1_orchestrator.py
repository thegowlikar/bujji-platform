"""Gate 1 unattended orchestrator. Measurement only; it cannot trade.

WHAT THIS IS ALLOWED TO DO: wait for a token that someone else delivered, run
the no-trade preflight, read a spot through the execution-neutered data path,
build the authoritative universe, run the bounded harness, seal the result,
and tell the operator what happened.

WHAT IT CANNOT DO, BY CONSTRUCTION rather than by intention: it never invokes
systemctl on any unit, never writes a sentinel file, never edits a drop-in,
and never imports an order path. The trading unit stays disabled and
condition-gated throughout; nothing here can change that, and
tests/dry-run case `no_trading_command_path` asserts the absence by AST.

FAIL CLOSED EVERYWHERE. A missing token, a malformed one, one that expires
before the window ends, a failed preflight, an unreadable spot, a refused
universe -- each ends the run with a sealed failure report. None of them ever
becomes "try again with the safety off".

NOTIFICATION IS NOT VERDICT. ntfy delivery is best-effort and its failure is
recorded but never allowed to change, hide, or delay the local result. A phone
that cannot be reached must not turn one failure into two.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
RUN_DIR = Path("/opt/bujji/gate1-run")
OUT_ROOT = Path("/opt/bujji/gate1")
CODE_ROOT = "/opt/bujji/work-m4"
ENV_FILE = Path("/opt/bujji/.env")
NOTIFY = Path("/opt/bujji/notify.sh")
LOCK_PATH = Path("/opt/bujji/gate1-run/.gate1.lock")

# Result categories, closed set. The operator summary and the ntfy body carry
# one of these and nothing else about the failure -- enough to know whether to
# reach for the laptop, never enough to leak what the box holds.
R_PASS = "GATE1_PASS"
R_FAIL = "GATE1_FAIL"
R_NO_TOKEN = "NO_VALID_TOKEN"
R_PREFLIGHT = "PREFLIGHT_FAILED"
R_NO_SPOT = "SPOT_UNAVAILABLE"
R_UNIVERSE = "UNIVERSE_REFUSED"
R_HARNESS = "HARNESS_FAILED"
R_DEADLINE = "WINDOW_MISSED"
R_INSUFFICIENT = "WINDOW_INSUFFICIENT"
R_NO_OPENING = "OPENING_COVERAGE_UNAVAILABLE"
R_LOCKED = "ALREADY_RUNNING"
R_INTERNAL = "INTERNAL_ERROR"

# THE COMPLETE LIFECYCLE BUDGET, in seconds.
#
# A measurement is not finished when the last callback arrives. It is finished
# when the corpus has been re-parsed offline, every artifact sealed, the
# verdict graded, and the operator told. Budgeting only the ramp and the
# sustained phase is how a run reaches 15:35 with an unsealed corpus and no
# verdict -- which is not evidence, whatever it cost to collect.
#
# Every figure below is an upper bound with slack, deliberately: overrunning
# the estimate wastes a session, and no estimate here is worth a partial run.
BUDGET = {
    "universe_build_s": 240,      # observed ~30s; the public master can be slow
    "connect_s": 60,              # websocket handshake before the bell
    "subscribe_s": 120,           # submitting the full provisional set
    "reconnect_s": 540,           # baseline + disconnect + after-window
    "offline_validation_s": 900,  # re-parse of a multi-GB corpus
    "sealing_s": 420,             # sha256 over every artifact
    "notification_s": 90,
    # Tail phases, reserved out of the capture window rather than bolted on
    # after it. Both end before the harness stop deadline.
    "reconnect_s": 540,
    "capacity_probe_s": 600,
    "safety_margin_s": 420,
}


# Minimum capture worth calling a session. Below this the run is a sample, not
# a measurement, and should not consume a trading day pretending otherwise.
MIN_CAPTURE_S = 3600

# THE CLOSE COMES FROM THE SINGLE AUTHORITY, NOT FROM A LITERAL HERE.
#
# This file declared --window-end 15:35, which matches NEITHER close. The cash
# segment closes 15:30; F&O closes 15:40 (NSE circular 2026-05-30, effective
# 2026-08-03). Bujji trades NIFTY options, i.e. F&O. bujji/market_calendar.py
# exists precisely because fifteen files had each invented their own close
# literal and disagreed -- and Gate 1 was frozen before it could be reconciled
# to it, making this the sixteenth.
#
# WHAT IT COST. harness_stop is window_end minus the sealing tail (23.5 min),
# so a 15:35 window stopped the capture at 15:11:30 and missed the last 28.5
# minutes of the F&O session -- including the close, which for options is the
# busiest window of the day. The measurement would have been reported as a
# full session.
#
# Imported lazily inside _derive_window_end so that supplying --window-end
# explicitly needs no import at all, and an import failure cannot take down a
# run whose window the operator already stated.
_HARNESS_TAIL_S = (BUDGET["offline_validation_s"] + BUDGET["sealing_s"]
                   + BUDGET["notification_s"])


def _derive_window_end() -> str:
    """window_end such that the harness stops exactly at the F&O close.

    harness_stop = window_end - _HARNESS_TAIL_S, so window_end must be the
    close PLUS that tail: capture runs to the bell and sealing follows it,
    rather than eating the last half hour of the session.

    Fails closed. If the authority cannot be imported this refuses rather than
    falling back to a literal -- a guessed close is the defect being fixed.
    """
    import sys as _sys
    if CODE_ROOT not in _sys.path:
        _sys.path.insert(0, CODE_ROOT)
    try:
        from bujji.market_calendar import FO_MARKET_CLOSE, SESSION_TIMES_SOURCE
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"CONFIG_ERROR: cannot import bujji.market_calendar from {CODE_ROOT} "
            f"({type(exc).__name__}: {exc}). Pass --window-end explicitly rather "
            f"than letting this guess a close time.")
    close_dt = datetime(2000, 1, 1, FO_MARKET_CLOSE.hour, FO_MARKET_CLOSE.minute,
                        FO_MARKET_CLOSE.second)
    end = close_dt + timedelta(seconds=_HARNESS_TAIL_S)
    log(f"window derived from FO_MARKET_CLOSE {FO_MARKET_CLOSE:%H:%M:%S} "
        f"+ {_HARNESS_TAIL_S}s sealing tail -> window-end {end:%H:%M:%S}; "
        f"capture therefore runs to the F&O close. Source: {SESSION_TIMES_SOURCE}")
    return end.strftime("%H:%M:%S")


def preopen_budget(now, subscribe_by) -> dict:
    """Can the provisional universe be built AND subscribed before the bell?

    This is the question the old model never asked. It budgeted the ramp and
    the sustained phase -- work that happens after the open -- and so it could
    approve a start that was already too late to be subscribed at 09:15.
    """
    need = BUDGET["universe_build_s"] + BUDGET["connect_s"] + BUDGET["subscribe_s"]
    have = (subscribe_by - now).total_seconds()
    return {"needed_s": need, "available_s": int(have), "fits": have >= need}


def lifecycle_budget(open_at, window_end) -> dict:
    """Seconds from the bell to the operator being told, under the new flow.

    Capture length is set by the WINDOW, not by a flag: recording starts at
    09:15 and stops early enough to leave room for offline validation, sealing
    and notification inside the session.
    """
    tail = (BUDGET["offline_validation_s"] + BUDGET["sealing_s"]
            + BUDGET["notification_s"] + BUDGET["safety_margin_s"]
            + BUDGET["reconnect_s"] + BUDGET["capacity_probe_s"])
    capture = (window_end - open_at).total_seconds() - tail
    return {"tail_s": tail, "capture_s": int(capture),
            "min_capture_s": MIN_CAPTURE_S, "fits": capture >= MIN_CAPTURE_S}


state: dict = {"events": [], "notify_failures": []}


def log(msg: str) -> None:
    stamp = datetime.now(IST).strftime("%H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    state["events"].append({"t": datetime.now(IST).isoformat(), "msg": msg})


def notify(title: str, body: str, priority: str = "default", tag: str = "satellite") -> None:
    """Best-effort push. NEVER raises, never changes the verdict.

    Bodies here are assembled from the closed result-category set and plain
    counts. No credential-adjacent value can reach this function, because none
    is ever placed in `state`.
    """
    if not NOTIFY.exists():
        state["notify_failures"].append({"at": datetime.now(IST).isoformat(),
                                         "reason": "notify.sh absent"})
        return
    try:
        r = subprocess.run([str(NOTIFY), title, body, priority, tag],
                           capture_output=True, text=True, timeout=25)
        if r.returncode != 0:
            state["notify_failures"].append(
                {"at": datetime.now(IST).isoformat(),
                 "reason": f"notify.sh exit {r.returncode}"})
    except Exception as exc:
        state["notify_failures"].append(
            {"at": datetime.now(IST).isoformat(),
             "reason": f"{type(exc).__name__}"})


def env_token_expiry() -> "datetime | None":
    """Expiry INSTANT only, decoded locally. The value is never returned.

    Reads the canonical destination the approved inbox receiver promotes into.
    Nothing here logs, echoes, or persists any part of the credential.
    """
    try:
        raw = ENV_FILE.read_text()
    except Exception:
        return None
    token = None
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("FYERS_ACCESS_TOKEN="):
            token = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
    if not token or token.count(".") < 2:
        return None
    try:
        body = token.split(".")[1]
        body += "=" * (-len(body) % 4)
        exp = json.loads(base64.urlsafe_b64decode(body)).get("exp")
        return datetime.fromtimestamp(int(exp), IST) if exp else None
    except Exception:
        return None
    finally:
        token = None                      # do not keep it alive in this frame


def wait_for_token(window_end: datetime, deadline: datetime, poll_s: int) -> bool:
    """Wait for a token that outlives the whole measurement window.

    The approved receiver (bujji-token-inbox.path -> promote_from_inbox.py)
    does the exchange and the promotion; this only observes the result. It
    never touches the inbox, never exchanges anything, and never writes to the
    credential destination.
    """
    log(f"awaiting a token valid through {window_end:%H:%M} IST "
        f"(deadline {deadline:%H:%M})")
    notify("Bujji Gate 1: awaiting token",
           f"Measurement will start once a token valid to "
           f"{window_end:%H:%M} IST arrives. Deadline {deadline:%H:%M}.",
           "default", "hourglass")
    announced = False
    while datetime.now(IST) < deadline:
        exp = env_token_expiry()
        if exp and exp >= window_end:
            log(f"token present and valid past the window (expires {exp:%Y-%m-%d %H:%M})")
            return True
        if exp and not announced:
            log("a token is present but expires before the window ends -- still waiting")
            announced = True
        time.sleep(poll_s)
    log("deadline reached with no sufficiently long-lived token")
    return False


def run(cmd: list, timeout: int, env: dict | None = None, cwd: str = str(RUN_DIR)):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=cwd, env=env)


def build_status_summary(res: dict, verdict: str, failures: list,
                         replay_certifiable: bool) -> str:
    """The one message that has to stand on its own.

    Sai is away from the laptop. This is not a nudge to go and read a JSON
    file -- it is the result, in the notification, with the figures that
    decide whether the day was worth anything. Every line is a measured
    number or an explicit "not established"; nothing here is credential
    adjacent, because nothing credential adjacent is ever placed in `res`.
    """
    L = []
    L.append(f"{'PASS' if verdict == 'PASS' else 'FAIL'} - Gate 1")

    # Coverage: the headline, and it is conditional by construction.
    cov = res.get("opening_coverage") or {}
    st = cov.get("status")
    if st == "CONTAINED":
        L.append(f"Coverage: canonical {cov.get('canonical_symbol_count')} "
                 f"inside provisional {cov.get('provisional_symbol_count')} "
                 f"- continuous from the open")
    elif st == "GAP":
        L.append(f"Coverage: GAP - {cov.get('missing_from_provisional_count')} "
                 f"canonical symbol(s) not pre-subscribed")
    else:
        L.append(f"Coverage: {st or 'not established'}")

    # Ingestion, and every way it can lose something.
    acc = (((res.get("feed_state") or {}).get("full") or {}).get("corpus") or {})
    if acc:
        L.append(f"Captured: {acc.get('written', 0):,} records, "
                 f"{acc.get('dropped', 0)} dropped, "
                 f"{acc.get('rejected', 0)} rejected")
        L.append(f"Queue peak: {acc.get('max_queue_depth', 0):,} / "
                 f"{acc.get('queue_capacity', 0):,}")

    # Rate, from the sustained phase.
    for ph in res.get("phases") or ():
        if ph.get("phase") == "opening_sustained":
            if ph.get("msgs_per_sec") is not None:
                L.append(f"Rate: {ph['msgs_per_sec']}/s across "
                         f"{ph.get('subscribed', 0)} symbols")
            sil = (ph.get("silent_symbols") or {}).get("counts") or {}
            if sil:
                L.append(f"Silent (coverage, not loss): "
                         + ", ".join(f"{k}={v}" for k, v in sorted(sil.items())))
            break

    for ph in res.get("phases") or ():
        if str(ph.get("phase", "")).endswith("_reconnect_after"):
            L.append(f"Reconnect: {ph.get('resumed_count')}/"
                     f"{ph.get('producers_before_reconnect')} producers resumed"
                     + (" - FULL" if ph.get("full_restoration") else " - PARTIAL"))
            break

    probe = res.get("capacity_probe") or {}
    if probe.get("highest_accepted"):
        L.append(f"Capacity: {probe['highest_accepted']} concurrent subs "
                 f"accepted (zero-gap needs ~1465)")
    elif probe:
        L.append("Capacity: probe did not complete")

    L.append(f"Replay: {'certified' if replay_certifiable else 'REFUSED to certify'}")

    if failures:
        L.append(f"Failures ({len(failures)}): {failures[0][:110]}")
        if len(failures) > 1:
            L.append(f"...and {len(failures) - 1} more - see the run summary")

    L.append("Harness feed evidence only. Not Bujji runtime evidence. "
             "Authorises no trading.")
    return "\n".join(L)


def seal_and_finish(session_dir: Path, category: str, detail: str,
                    started: datetime, exit_code: int) -> int:
    """Durable operator-readable summary. Written even on the worst path."""
    session_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema": "gate1.run_summary/1",
        "result_category": category,
        "detail": detail,
        "exit_code": exit_code,
        "started_ist": started.isoformat(),
        "finished_ist": datetime.now(IST).isoformat(),
        "duration_s": round((datetime.now(IST) - started).total_seconds(), 1),
        "host": os.uname().nodename,
        "pid": os.getpid(),
        "invoked_as": " ".join(sys.argv),
        "artifact_dir": str(session_dir),
        "events": state["events"],
        "notify_failures": state["notify_failures"],
        "notification_delivery": ("degraded" if state["notify_failures"] else "ok"),
        "notification_never_affects_verdict": True,
        "trading_units_touched": "none -- this orchestrator issues no systemctl "
                                 "command and writes no gate sentinel",
    }
    path = session_dir / "gate1_run_summary.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(summary, indent=1, sort_keys=True))
    tmp.replace(path)
    log(f"summary sealed: {path}  [{category}]")
    return exit_code


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--measurement-date", required=True)
    # STRICT PRE-OPEN CUTOFF. Not an acceptance window: everything after this
    # must still fit -- build the provisional universe, connect, and submit
    # every subscription -- before the bell. A token later than this cannot
    # produce opening coverage, so the run is abandoned rather than started
    # late and presented as full-session evidence.
    ap.add_argument("--token-deadline", default="09:02")
    ap.add_argument("--subscribe-by", default="09:14:30")
    ap.add_argument("--opening-buffer-points", type=int, default=500)
    ap.add_argument("--capacity-probe", action="store_true",
                    help="run a bounded subscription-capacity probe as the "
                         "FINAL phase, after every measured phase")
    ap.add_argument("--capacity-steps", default="500,750,1000,1250,1465")
    ap.add_argument("--open-at", default="09:15:00")
    ap.add_argument("--spot-after", default="09:18")
    ap.add_argument("--window-end", default=None,
                    help="Everything-done-by time. Omit to derive it from "
                         "bujji.market_calendar.FO_MARKET_CLOSE plus the "
                         "sealing tail, so capture runs to the F&O close.")
    ap.add_argument("--sustained-minutes", type=int, default=300)
    ap.add_argument("--phase-seconds", type=int, default=300)
    ap.add_argument("--token-poll-seconds", type=int, default=20)
    ap.add_argument("--dry-run", default=None,
                    help="exercise one failure path without touching the market")
    args = ap.parse_args()

    started = datetime.now(IST)
    session_dir = OUT_ROOT / args.session_id
    day = datetime.strptime(args.measurement_date, "%Y-%m-%d").date()

    def at(hhmm: str) -> datetime:
        parts = [int(x) for x in hhmm.split(":")]
        while len(parts) < 3:
            parts.append(0)
        h, m, sec = parts
        return datetime.combine(
            day, datetime.min.time().replace(hour=h, minute=m, second=sec),
            tzinfo=IST)

    window_end = at(args.window_end or _derive_window_end())
    token_deadline = at(args.token_deadline)
    open_at = at(args.open_at)
    subscribe_by = at(args.subscribe_by)

    # The universe is not built yet, so the exact ramp length is unknown. Six
    # rungs is what derive_ramp() produces for any realistic universe, and
    # over-estimating costs nothing but caution.
    budget = lifecycle_budget(open_at, window_end)

    # TOKEN VALIDITY MUST REACH THE END OF THE LIFECYCLE, not the end of the
    # last callback. A token that dies during offline validation leaves a
    # corpus nobody can seal and a verdict nobody can grade.
    # Validity must reach past sealing AND notification, not the last callback:
    # a token that dies during offline validation leaves a corpus nobody can
    # seal and a verdict nobody can grade.
    token_must_outlive = window_end + timedelta(
        seconds=BUDGET["offline_validation_s"] + BUDGET["sealing_s"]
        + BUDGET["notification_s"])

    # ---- single-instance lock -------------------------------------------
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another measurement holds the lock -- refusing to start a second")
        notify("Bujji Gate 1: already running",
               "A second start was refused; one harness at a time.", "low", "lock")
        return seal_and_finish(session_dir, R_LOCKED,
                               "another instance holds the lock", started, 3)
    lock_fh.write(f"{os.getpid()}\n{started.isoformat()}\n")
    lock_fh.flush()

    try:
        # ---- deadline sanity (survives a reboot into the wrong hour) -----
        if datetime.now(IST) > window_end:
            log("started after the measurement window closed")
            notify("Bujji Gate 1: window missed", f"{R_DEADLINE}", "high", "warning")
            return seal_and_finish(session_dir, R_DEADLINE,
                                   "started after window end", started, 4)

        env = dict(os.environ)
        env.update({"GATE1_SESSION_ID": args.session_id,
                    "GATE1_MEASUREMENT_DATE": args.measurement_date})

        # ---- 1. token ----------------------------------------------------
        if args.dry_run == "no_token":
            ok = False
        elif args.dry_run in ("mock_pass", "preflight_fail", "harness_crash",
                              "notify_fail", "no_spot"):
            ok = True
        else:
            ok = wait_for_token(token_must_outlive, token_deadline,
                                args.token_poll_seconds)
        if not ok:
            notify("Bujji Gate 1: no measurement", f"{R_NO_TOKEN}. No token valid "
                   f"for the window arrived before the deadline.", "high", "warning")
            return seal_and_finish(session_dir, R_NO_TOKEN,
                                   "no sufficiently long-lived token by deadline",
                                   started, 5)

        # Load the promoted credential into this process's environment only.
        if ENV_FILE.exists() and args.dry_run is None:
            for line in ENV_FILE.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")

        # ---- 1b. can this still produce OPENING coverage? ----------------
        #
        # Two questions, and the first one is the one the previous design
        # skipped: is there time to build the provisional universe, connect,
        # and submit every subscription BEFORE the bell? A run that answers no
        # cannot produce opening coverage, and starting it anyway would yield a
        # measurement with a hole in it that reads like a clean one.
        now = datetime.now(IST)
        pre = preopen_budget(now, subscribe_by)
        state["events"].append({"t": now.isoformat(), "msg": f"pre-open budget: {pre}"})
        if not pre["fits"]:
            log(f"CANNOT SUBSCRIBE BEFORE THE BELL: {pre['available_s']}s left, "
                f"{pre['needed_s']}s needed")
            notify("Bujji Gate 1: no opening coverage",
                   f"{R_NO_OPENING}. The token arrived too late to subscribe "
                   f"before 09:15. Abandoned rather than started late.",
                   "urgent", "warning")
            return seal_and_finish(
                session_dir, R_NO_OPENING,
                f"pre-open budget: needed {pre['needed_s']}s, had "
                f"{pre['available_s']}s", started, 20)

        if not budget["fits"]:
            log(f"session too short: {budget['capture_s']}s of capture available")
            notify("Bujji Gate 1: no measurement",
                   f"{R_INSUFFICIENT}. Too little session to capture and seal.",
                   "high", "warning")
            return seal_and_finish(session_dir, R_INSUFFICIENT,
                                   f"capture window {budget['capture_s']}s "
                                   f"< minimum {MIN_CAPTURE_S}s", started, 12)
        log(f"budget OK: subscribe within {pre['available_s']}s, then "
            f"{budget['capture_s']}s of capture")

        # ---- 2. preflight ------------------------------------------------
        log("running the no-trade preflight")
        if args.dry_run == "preflight_fail":
            pf = subprocess.CompletedProcess([], 1, "", "dry-run forced failure")
        elif args.dry_run in ("mock_pass", "harness_crash", "notify_fail", "no_spot"):
            pf = subprocess.CompletedProcess([], 0, "dry-run mocked PASS", "")
        else:
            pf = run(["/opt/bujji/.venv/bin/python", "gate1_preflight.py"], 300, env)
        (session_dir).mkdir(parents=True, exist_ok=True)
        (session_dir / "preflight_output.txt").write_text(
            (pf.stdout or "") + (pf.stderr or ""))
        if pf.returncode != 0:
            log(f"PREFLIGHT FAILED (exit {pf.returncode}) -- not measuring")
            notify("Bujji Gate 1: PREFLIGHT FAILED",
                   f"{R_PREFLIGHT}. No measurement was attempted. Nothing was "
                   f"changed and trading remains disabled.", "urgent", "no_entry")
            return seal_and_finish(session_dir, R_PREFLIGHT,
                                   f"preflight exit {pf.returncode}", started, 6)
        log("preflight PASS")
        notify("Bujji Gate 1: preflight PASS",
               "No-trade isolation verified. Waiting for open.", "default", "white_check_mark")

        # ---- 3. PRE-OPEN: prior reference price --------------------------
        #
        # Read through the SAME execution-neutered path used post-open. Before
        # the bell this returns the prior session's last traded level, which is
        # exactly what a provisional band should be centred on -- documented as
        # such, never passed off as an opening price.
        if datetime.now(IST) >= subscribe_by:
            log("already past the subscription deadline -- no opening coverage")
            notify("Bujji Gate 1: no measurement",
                   f"{R_NO_OPENING}. Too late to subscribe before the bell.",
                   "urgent", "warning")
            return seal_and_finish(session_dir, R_NO_OPENING,
                                   "past the subscription deadline at start",
                                   started, 20)

        log("reading the PRIOR-SESSION reference price (read-only path)")
        if args.dry_run in ("mock_pass", "harness_crash", "notify_fail"):
            sp = subprocess.CompletedProcess([], 0, "24000.0", "{}")
        elif args.dry_run == "no_spot":
            sp = subprocess.CompletedProcess([], 2, "", "forced")
        else:
            sp = run(["/opt/bujji/.venv/bin/python", "gate1_spot.py",
                      "--code-root", CODE_ROOT,
                      "--out", str(session_dir / "prior_reference_provenance.json")],
                     180, env)
        if sp.returncode != 0 or not (sp.stdout or "").strip():
            log("prior reference unavailable -- refusing to guess a band centre")
            notify("Bujji Gate 1: no measurement",
                   f"{R_NO_SPOT}. The read-only reference source failed.",
                   "urgent", "warning")
            return seal_and_finish(session_dir, R_NO_SPOT,
                                   "prior reference unavailable", started, 7)
        prior = (sp.stdout or "").strip().splitlines()[0]
        log(f"prior-session reference: {prior}")

        # ---- 4. PROVISIONAL universe, buffered ---------------------------
        log(f"building the PROVISIONAL universe (buffer "
            f"{args.opening_buffer_points} points)")
        if args.dry_run in ("mock_pass", "harness_crash", "notify_fail"):
            ub = subprocess.CompletedProcess([], 0, "mocked", "")
        else:
            ub = run(["/opt/bujji/.venv/bin/python", "gate1_build_universe.py",
                      "--provisional",
                      "--opening-buffer-points", str(args.opening_buffer_points),
                      "--spot", prior,
                      "--spot-source",
                      f"PRIOR-SESSION reference via FyersBroker.get_spot through "
                      f"disable_live_execution, read {datetime.now(IST):%H:%M:%S} "
                      f"IST (pre-open; last traded level, NOT an opening price)",
                      "--session-id", args.session_id,
                      "--code-root", CODE_ROOT, "--out", str(OUT_ROOT)], 600, env)
        (session_dir / "provisional_build_output.txt").write_text(
            (ub.stdout or "") + (ub.stderr or ""))
        if ub.returncode != 0:
            notify("Bujji Gate 1: no measurement",
                   f"{R_UNIVERSE}. The provisional build was refused.",
                   "urgent", "warning")
            return seal_and_finish(session_dir, R_UNIVERSE,
                                   f"provisional builder exit {ub.returncode}",
                                   started, 8)
        prov_path = session_dir / "provisional_universe.json"
        prov_n = None
        try:
            prov_n = json.loads(prov_path.read_text())["measured_symbol_count"]
        except Exception:
            pass
        log(f"provisional universe: {prov_n} symbols")
        notify("Bujji Gate 1: provisional universe ready",
               f"{prov_n} symbols, buffered {args.opening_buffer_points} pts "
               f"around the prior reference. Subscribing before the bell.",
               "default", "abacus")

        # ---- 5. OPENING CAPTURE: subscribe pre-open, record from 09:15 ----
        harness_stop = window_end - timedelta(
            seconds=BUDGET["offline_validation_s"] + BUDGET["sealing_s"]
            + BUDGET["notification_s"])
        handoff = session_dir / "canonical_universe_handoff.json"
        log(f"starting opening capture (subscribe by {subscribe_by:%H:%M:%S}, "
            f"stop {harness_stop:%H:%M})")
        notify("Bujji Gate 1: subscriptions prepared",
               f"Connecting and subscribing {prov_n} symbols before "
               f"{subscribe_by:%H:%M:%S} IST.", "default", "satellite")

        if args.dry_run in ("mock_pass", "notify_fail"):
            hz = subprocess.CompletedProcess([], 0, "mocked opening capture", "")
        elif args.dry_run == "harness_crash":
            hz = subprocess.CompletedProcess([], 139, "", "forced")
        else:
            proc = subprocess.Popen(
                ["/opt/bujji/.venv/bin/python", "gate1_opening_capture.py",
                 "--provisional-universe", str(prov_path),
                 "--canonical-handoff", str(handoff),
                 "--first-spot-out", str(session_dir / "first_post_open_spot.json"),
                 "--out", str(session_dir),
                 "--open-at", open_at.isoformat(),
                 "--subscribe-by", subscribe_by.isoformat(),
                 "--stop-deadline", harness_stop.isoformat(),
                 "--reconnect-test",
                 "--reconnect-seconds", str(BUDGET["reconnect_s"]),
                 "--capacity-probe-seconds", str(BUDGET["capacity_probe_s"]),
                 "--capacity-steps", args.capacity_steps]
                + (["--capacity-pool", str(session_dir / "capacity_pool.json")]
                   if args.capacity_probe else []),
                cwd=str(RUN_DIR), env=env,
                stdout=open(session_dir / "harness_output.txt", "w"),
                stderr=subprocess.STDOUT)

            # ---- 5b. POST-OPEN: refine on the FIRST spot CALLBACK --------
            #
            # Triggered by the stream, not by a timer. The previous version
            # slept 180s and then made a REST call; the wait bought nothing
            # (coverage is already decided by what was subscribed before the
            # bell) and left containment unknown three minutes longer than it
            # needed to be. The harness writes first_spot.json the instant a
            # valid post-open spot callback arrives; this polls for it.
            while datetime.now(IST) < open_at:
                time.sleep(1)
            notify("Bujji Gate 1: opening capture started",
                   f"Recording from the bell across {prov_n} symbols.",
                   "default", "bell")

            first_spot_path = session_dir / "first_post_open_spot.json"
            waited = 0.0
            while not first_spot_path.exists() and waited < 900:
                if proc.poll() is not None:
                    break
                time.sleep(0.5)
                waited += 0.5

            live = None
            if first_spot_path.exists():
                try:
                    fs = json.loads(first_spot_path.read_text())
                    live = str(fs["spot"])
                    log(f"first post-open spot callback: {live} "
                        f"(+{fs['seconds_after_open']}s after the bell)")
                except Exception as exc:
                    log(f"first-spot file unreadable: {type(exc).__name__}")
            else:
                log("no valid post-open spot callback arrived; containment "
                    "cannot be established from the stream")

            if live:
                cb = run(["/opt/bujji/.venv/bin/python", "gate1_build_universe.py",
                          "--spot", live,
                          "--spot-source",
                          f"FIRST VALID POST-OPEN SPOT CALLBACK from the SDK "
                          f"boundary at {datetime.now(IST):%H:%M:%S} IST",
                          "--session-id", args.session_id,
                          "--code-root", CODE_ROOT, "--out", str(OUT_ROOT)], 600, env)
                if cb.returncode == 0:
                    canon_src = session_dir / "universe.json"
                    tmp = handoff.with_suffix(".tmp")
                    tmp.write_text(canon_src.read_text())
                    tmp.replace(handoff)         # atomic handoff
                    log("canonical universe handed to the capture harness")
                else:
                    log("canonical build failed; containment cannot be proven")

            # ---- capacity pool: the role expiries IN FULL ----------------
            #
            # Built through the SAME canonical builder with a band wide enough
            # to admit every listed strike, so the probe draws from the real
            # zero-gap candidate set rather than an invented list. Only the
            # three role expiries appear, because only they can ever enter the
            # canonical universe.
            if args.capacity_probe:
                pb = run(["/opt/bujji/.venv/bin/python", "gate1_build_universe.py",
                          "--provisional", "--opening-buffer-points", "99000",
                          "--spot", live or prior,
                          "--spot-source",
                          "CAPACITY POOL: role expiries in full (band widened "
                          "past every listed strike); not a capture universe",
                          "--session-id", args.session_id + "_POOL",
                          "--code-root", CODE_ROOT, "--out", str(OUT_ROOT)], 600, env)
                pool_src = OUT_ROOT / (args.session_id + "_POOL") / "provisional_universe.json"
                if pb.returncode == 0 and pool_src.exists():
                    pool_dst = session_dir / "capacity_pool.json"
                    pool_dst.write_text(pool_src.read_text())
                    try:
                        pool_n = json.loads(pool_dst.read_text())["measured_symbol_count"]
                        log(f"capacity pool ready: {pool_n} symbols "
                            f"(role expiries in full)")
                    except Exception:
                        pass
                else:
                    log("capacity pool build failed; probe will be skipped")

            proc.wait(timeout=int((window_end - datetime.now(IST)).total_seconds()) + 3600)
            hz = subprocess.CompletedProcess([], proc.returncode, "", "")

        if hz.returncode == 20:
            notify("Bujji Gate 1: no opening coverage",
                   f"{R_NO_OPENING}. The run was abandoned rather than started "
                   f"late. Nothing is presented as full-session evidence.",
                   "urgent", "warning")
            return seal_and_finish(session_dir, R_NO_OPENING,
                                   "harness reported no opening coverage",
                                   started, 20)
        if hz.returncode not in (0, 10):
            notify("Bujji Gate 1: harness failure",
                   f"{R_HARNESS} (exit {hz.returncode}). Artifacts preserved.",
                   "urgent", "rotating_light")
            return seal_and_finish(session_dir, R_HARNESS,
                                   f"harness exit {hz.returncode}", started, 9)

        # ---- 5c. OFFLINE REPLAY: read the corpus back and verify it ------
        #
        # Until this existed the corpus was written and never read. Every
        # figure Gate 1 reported was computed live and in memory; the file was
        # only ever counted for parseable lines. An artifact nobody reads is an
        # artifact nobody has checked.
        log("replaying the corpus offline (verify, then analyse)")
        rp = run(["/opt/bujji/.venv/bin/python", "gate1_replay.py",
                  "--session-dir", str(session_dir)], 1800, env)
        (session_dir / "replay_output.txt").write_text((rp.stdout or "") + (rp.stderr or ""))
        replay_certifiable = rp.returncode == 0
        log(f"replay {'CERTIFIABLE' if replay_certifiable else 'NOT certifiable'}")

        # ---- 6. grade -----------------------------------------------------
        verdict, failures, reconnect, containment = "UNKNOWN", [], None, None
        res = {}
        try:
            newest = max((p for p in session_dir.rglob("gate1_results.json")),
                         key=lambda p: p.stat().st_mtime)
            res = json.loads(newest.read_text())
            verdict = res.get("gate1_verdict", {}).get("verdict", "UNKNOWN")
            failures = res.get("gate1_verdict", {}).get("failures", [])
            containment = (res.get("opening_coverage") or {}).get("status")
            probe = res.get("capacity_probe") or {}
            if probe.get("highest_accepted"):
                log(f"capacity probe: highest accepted "
                    f"{probe['highest_accepted']} concurrent subscriptions")
                notify("Bujji Gate 1: capacity probe",
                       f"Highest subscription count that produced data: "
                       f"{probe['highest_accepted']}. Zero-gap needs ~1465. "
                       f"Evidence only - changes no Gate 1 verdict.",
                       "default", "straight_ruler")
            for ph in res.get("phases") or ():
                if str(ph.get("label", "")).endswith("_reconnect_after"):
                    reconnect = ("full restoration"
                                 if ph.get("full_restoration") else "NOT restored")
        except Exception as exc:
            log(f"could not read the graded result: {type(exc).__name__}")
        if containment:
            notify("Bujji Gate 1: containment "
                   + ("PASS" if containment == "CONTAINED" else "FAIL"),
                   f"Canonical universe {containment}. "
                   + ("Covered continuously from the open."
                      if containment == "CONTAINED"
                      else "An opening gap was detected and is graded FAIL."),
                   "default" if containment == "CONTAINED" else "high",
                   "white_check_mark" if containment == "CONTAINED" else "x")
        if reconnect:
            notify("Bujji Gate 1: reconnect", f"Reconnect: {reconnect}.",
                   "default", "electric_plug")
        if not replay_certifiable:
            failures = list(failures) + ["offline replay refused to certify the corpus"]
            verdict = "FAIL"
        log(f"GATE 1 VERDICT: {verdict} ({len(failures)} failure(s))")
        try:
            summary_body = build_status_summary(res, verdict, list(failures),
                                                replay_certifiable)
        except Exception as exc:  # noqa: BLE001 -- never lose the result
            summary_body = (f"{verdict} - Gate 1, {len(failures)} failure(s). "
                            f"Summary could not be composed "
                            f"({type(exc).__name__}); the sealed run summary "
                            f"has the detail.")
        notify(f"Bujji Gate 1 {verdict}: session summary", summary_body,
               "high" if verdict != "PASS" else "default",
               "white_check_mark" if verdict == "PASS" else "x")
        (session_dir / "status_summary.txt").write_text(summary_body)
        return seal_and_finish(session_dir,
                               R_PASS if verdict == "PASS" else R_FAIL,
                               f"verdict={verdict} failures={len(failures)}",
                               started, 0 if verdict == "PASS" else 10)

    except Exception as exc:
        log(f"INTERNAL ERROR: {type(exc).__name__}: {exc}")
        notify("Bujji Gate 1: internal error",
               f"{R_INTERNAL}. The run stopped; nothing was changed and trading "
               f"remains disabled.", "urgent", "rotating_light")
        return seal_and_finish(session_dir, R_INTERNAL,
                               f"{type(exc).__name__}", started, 11)
    finally:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
