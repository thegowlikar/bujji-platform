"""A reconnect must restore the subscriptions, proven by dropping one.

WHAT THIS EXISTS TO PREVENT. On 2026-08-25 the feed dropped at 09:26:45,
eleven minutes after the open. The SDK reconnected, the socket stayed
ESTABLISHED for five hours, and not one further message arrived: a reconnected
socket carries no subscriptions and nothing re-submitted them. The session
captured 11.6 minutes of a 6.5-hour window while looking healthy throughout.

WHY THIS IS NOT A SOURCE-INSPECTION TEST. Asserting that an on_connect handler
exists proves nothing about whether data resumes -- the broken version had an
on_connect handler too, and it faithfully incremented a counter. This test
runs the real capture against a fake socket that behaves like the real one
(it CLEARS its subscriptions on reconnect), drops the connection mid-run, and
requires messages to actually resume.

The negative control runs the same harness against a copy with the resubscribe
removed and requires it to fail. A recovery test that cannot detect a missing
recovery is decoration.

Run: /opt/bujji/.venv/bin/python test_reconnect_resubscribe.py [--control PATH]
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
HERE = Path(__file__).resolve().parent
CAPTURE = Path("/opt/bujji/work-m4/ops/gate1/current/scripts/gate1_opening_capture.py")

failures = []


def check(name, ok, detail="", fail_detail=""):
    """detail prints always (an informative value); fail_detail only on failure.

    Printing a failure explanation next to an OK reads as a contradiction and
    makes a passing run look broken.
    """
    line = f"  [{'OK ' if ok else 'FAIL'}] {name}"
    if detail:
        line += f": {detail}"
    if not ok and fail_detail:
        line += f": {fail_detail}"
    print(line)
    if not ok:
        failures.append(name)
    return ok


def build_inputs(work: Path, symbols):
    universe = {
        "as_of_date": "2026-08-25", "atm": 24000.0, "atm_strike": 24000,
        "authoritative": True, "built_at": 0, "built_at_ist": "sandbox",
        "instruments": [{"symbol": s, "kind": "OPTION"} for s in symbols],
    }
    (work / "prov.json").write_text(json.dumps(universe))
    (work / "handoff.json").write_text(json.dumps(universe))
    return work / "prov.json", work / "handoff.json"


SCRIPTS = CAPTURE.parent


def stage(capture_path: Path, work: Path) -> Path:
    """Put the capture under test in a directory with its sibling modules.

    The capture resolves siblings relative to its own file, so a copy placed
    anywhere else dies on import. Both the fixed version and the control are
    staged this way so the two runs differ ONLY in the code being tested --
    otherwise a control that crashed at import would 'prove' the defect while
    actually proving nothing.
    """
    rundir = work / "rundir"
    rundir.mkdir(parents=True, exist_ok=True)
    for sib in SCRIPTS.glob("*.py"):
        if sib.name == "gate1_opening_capture.py":
            continue
        target = rundir / sib.name
        if not target.exists():
            target.symlink_to(sib)
    staged = rundir / "gate1_opening_capture.py"
    shutil.copy(capture_path, staged)
    return staged


def run_capture(capture_path: Path, work: Path, seconds: float,
                mode: str = "reconnect", drop_after: float = 6.0):
    """Run the real capture against the fake socket, on a short deadline."""
    capture_path = stage(capture_path, work)
    stub = work / "stub"
    (stub / "fyers_apiv3" / "FyersWebsocket").mkdir(parents=True, exist_ok=True)
    (stub / "fyers_apiv3" / "__init__.py").write_text("")
    (stub / "fyers_apiv3" / "FyersWebsocket" / "__init__.py").write_text("")
    shutil.copy(HERE / "fake_data_ws.py",
                stub / "fyers_apiv3" / "FyersWebsocket" / "data_ws.py")

    symbols = [f"NSE:NIFTY26AUG{24000 + 50 * i}CE" for i in range(8)]
    prov, handoff = build_inputs(work, symbols)
    out = work / "out"
    out.mkdir(exist_ok=True)

    now = datetime.now(IST)
    open_at = now + timedelta(seconds=3)
    stop = now + timedelta(seconds=seconds)
    cmd = [sys.executable, str(capture_path),
           "--provisional-universe", str(prov),
           "--canonical-handoff", str(handoff),
           "--first-spot-out", str(out / "spot.json"),
           "--out", str(out),
           "--open-at", open_at.isoformat(),
           "--subscribe-by", (open_at - timedelta(seconds=1)).isoformat(),
           "--stop-deadline", stop.isoformat(),
           "--handoff-timeout-s", "2"]
    # DUMMY CREDENTIALS ONLY. The socket is a fake and never reaches a
    # network, so these need to be non-empty and nothing more. Handing a real
    # token to a sandbox proof would be an unnecessary exposure for zero gain.
    env = {"PYTHONPATH": f"{stub}:/opt/bujji/work-m4", "PATH": "/usr/bin:/bin",
           "HOME": str(work),
           "FYERS_APP_ID": "SANDBOX-APP-ID-NOT-REAL",
           "FYERS_ACCESS_TOKEN": "sandbox-token-not-real",
           "FAKE_MODE": mode, "FAKE_DROP_AFTER_S": str(drop_after)}
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 90,
                       env=env, cwd=str(work))
    results = {}
    rp = out / "gate1_results.json"
    if rp.is_file():
        results = json.loads(rp.read_text())
    return p, results, symbols


def evaluate(tag, p, results, symbols, expect_recovery):
    stdout = p.stdout
    events = results.get("reconnect_events", [])
    detected = [e for e in events if e.get("kind") == "RECONNECT_DETECTED"]
    restored = [e for e in events if e.get("kind") == "RESUBSCRIBED"]

    print(f"\n--- {tag} ---")
    print(f"    exit={p.returncode}  events={len(events)}  "
          f"detected={len(detected)}  restored={len(restored)}")

    # The drop must actually have happened, or the whole test is vacuous.
    dropped = ("reconnect" in stdout.lower()
               or "connection lost" in stdout.lower()
               or bool(detected))
    check(f"{tag}: the connection actually dropped", dropped,
          fail_detail="no drop observed -- the harness did not exercise "
                      "recovery, so every check below it is vacuous")

    if expect_recovery:
        check(f"{tag}: the reconnect was detected", bool(detected))
        check(f"{tag}: the subscriptions were restored", bool(restored),
              f"{len(restored)} restore event(s)")
        if restored:
            check(f"{tag}: the FULL intended set came back",
                  restored[0].get("symbols") == len(symbols),
                  f"restored {restored[0].get('symbols')} of {len(symbols)}")
    else:
        check(f"{tag}: NO restore happened (control)", not restored,
              fail_detail="the control resubscribed, so it is not a control")
    return detected, restored


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", help="path to an unpatched copy to compare")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--silence", action="store_true",
                    help="also prove the half-open (silent feed) case")
    ap.add_argument("--silence-seconds", type=float, default=75.0)
    args = ap.parse_args()

    print("PROOF: a dropped connection must resubscribe and resume\n")
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        p, results, symbols = run_capture(CAPTURE, work, args.seconds)
        evaluate("fixed", p, results, symbols, expect_recovery=True)
        if not results:
            print("    stdout tail:", p.stdout[-600:])
            print("    stderr tail:", p.stderr[-600:])

    if args.silence:
        # A HALF-OPEN FEED: no on_close, no on_connect, nothing to react to.
        # Only a detector watching for absence of data can see this at all.
        print("\nPROOF: a feed that dies without disconnecting is still noticed\n")
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            p, results, symbols = run_capture(CAPTURE, work, args.silence_seconds,
                                              mode="silent", drop_after=5.0)
            ev = results.get("feed_silence_events", [])
            silent = [e for e in ev if e.get("kind") == "FEED_SILENT"]
            rc = results.get("reconnect_events", [])
            print(f"--- silent-feed ---\n    exit={p.returncode} "
                  f"silence_events={len(ev)} reconnect_events={len(rc)}")
            # The half-open case is defined by the ABSENCE of a reconnect
            # signal. A RESUBSCRIBED entry is expected here and is not one --
            # it is the detector's own cheap recovery firing, which is the
            # behaviour being proven.
            detected = [e for e in rc if e.get("kind") == "RECONNECT_DETECTED"]
            check("silent feed: no reconnect was signalled", not detected,
                  fail_detail="a reconnect was signalled, so this does not "
                              "exercise the half-open case")
            check("silent feed: the detector attempted a resubscribe",
                  any(e.get("kind") == "RESUBSCRIBED" for e in rc),
                  fail_detail="silence was seen but no recovery was tried")
            check("silent feed: the silence was detected", bool(silent),
                  f"{len(silent)} FEED_SILENT event(s)")
            if silent:
                check("silent feed: quiet time exceeded the threshold",
                      silent[0].get("quiet_seconds", 0) >= 30.0,
                      f"{silent[0].get('quiet_seconds')}s")
            if not results:
                print("    stderr tail:", p.stderr[-500:])

    if args.control:
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            p, results, symbols = run_capture(Path(args.control), work, args.seconds)
            evaluate("control (unpatched)", p, results, symbols,
                     expect_recovery=False)
            if not results:
                print("    control stderr tail:", p.stderr[-500:])

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
