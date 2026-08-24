"""The preflight must not demand an artifact that does not exist yet.

WHAT THIS LOCKS OUT. On 2026-08-24 bujji-gate1.service failed at 08:45:02 with
exit 6 PREFLIGHT_FAILED. Twenty-two of twenty-three checks passed. The one
failure was "universe built -- universe.json MISSING, run
gate1_build_universe.py after open", raised by a preflight that runs BEFORE
the bell against an artifact the orchestrator writes at step 6, after the
capture harness has already been launched, from the first valid post-open spot
callback. The check demanded, before open, a file its own orchestrator could
only produce after open -- and said so in its own message.

The one-shot timer disabled itself on the way out, so nothing retried, and the
day's opening coverage was lost. Intraday option-chain data does not backfill.

THE ASYMMETRY IS THE FIX, and it is what these tests pin. Absence is tolerated
before the artifact is due; PRESENCE IS ALWAYS VALIDATED IN FULL. A phase flag
that simply skipped the block would have traded an ordering bug for a silent
one -- a malformed universe would sail through whenever the flag was set.

Run: /opt/bujji/.venv/bin/python /opt/bujji/gate1-run/test_preflight_ordering.py
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PREFLIGHT = "/opt/bujji/gate1-run/gate1_preflight.py"
OUT_ROOT = Path("/opt/bujji/gate1")
PY = "/opt/bujji/.venv/bin/python"

# A session id that is obviously a test, carries a date so the
# measurement-date check is satisfied, and cannot collide with a real run.
SESSION = "GATE1_20260824_PREFLIGHT_SELFTEST"
SESSION_DIR = OUT_ROOT / SESSION

# A real universe to copy in for the presence case. Built by the canonical
# builder, so the ten downstream checks have something genuine to grade
# rather than a fixture that only resembles one.
REAL_UNIVERSE = OUT_ROOT / "GATE1_20260824_POSTOPEN"

failures = []


def check(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(name)
    return ok


def run_preflight(expect_universe):
    env = dict(os.environ)
    env["GATE1_SESSION_ID"] = SESSION
    env["GATE1_MEASUREMENT_DATE"] = "2026-08-24"
    if expect_universe:
        env["GATE1_EXPECT_UNIVERSE"] = "1"
    else:
        env.pop("GATE1_EXPECT_UNIVERSE", None)
    p = subprocess.run([PY, PREFLIGHT], capture_output=True, text=True,
                       timeout=180, env=env)
    out = p.stdout + p.stderr
    # EXACT CHECK NAME, AND ONLY A CHECK LINE.
    #
    # An earlier version of this test matched the substring "universe built"
    # and kept the LAST hit. That matched two other lines -- the neighbouring
    # check "universe built today", and the "- universe built: ..." entry in
    # the blocking-issues summary -- so the presence cases were graded on a
    # different check than the one under test, and passed for the wrong
    # reason. Anchor on the rendered check line and the exact name.
    verdict = None
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped.startswith("["):
            continue
        for status in ("[OK ] ", "[FAIL] ", "[warn] "):
            if stripped.startswith(status):
                name = stripped[len(status):].split(":", 1)[0]
                if name == "universe built":
                    verdict = stripped
    return out, verdict


def universe_check_status(verdict_line):
    """OK / FAIL / absent, read from the preflight's own printed line."""
    if verdict_line is None:
        return "absent"
    if verdict_line.startswith("[OK"):
        return "OK"
    if verdict_line.startswith("[FAIL"):
        return "FAIL"
    return "other"


print("PREFLIGHT ORDERING SELF TEST")
print()

shutil.rmtree(SESSION_DIR, ignore_errors=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)

try:
    # ---- 1. the defect itself: pre-open, artifact not yet due -------------
    print("universe ABSENT, no GATE1_EXPECT_UNIVERSE (the 08:45 case)")
    out, verdict = run_preflight(expect_universe=False)
    check("the universe check does not fail", universe_check_status(verdict) == "OK",
          verdict or "no universe line printed")
    check("it is reported as not yet due, not as present",
          "NOT YET DUE" in (verdict or ""),
          "absence is stated, never implied by silence")
    check("the run is not blocked by the universe",
          not any("universe built" in l for l in out.splitlines()
                  if l.strip().startswith("- ")),
          "universe is absent from the blocking-issues list")

    # ---- 2. the check is still real when the artifact IS expected --------
    print()
    print("universe ABSENT, GATE1_EXPECT_UNIVERSE=1 (the manual build-then-measure case)")
    out, verdict = run_preflight(expect_universe=True)
    check("a missing universe is fatal when expected",
          universe_check_status(verdict) == "FAIL", verdict or "no line")
    check("it appears in the blocking issues",
          any("universe built" in l for l in out.splitlines()
              if l.strip().startswith("- ")),
          "the operator is told what is missing")

    # ---- 3. THE ASYMMETRY: presence is validated in every phase ----------
    if not (REAL_UNIVERSE / "universe.json").exists():
        print()
        print("  [skip] no real universe available to copy; presence cases not run")
    else:
        for name in ("universe.json", "universe_manifest.json"):
            src = REAL_UNIVERSE / name
            if src.exists():
                shutil.copy(src, SESSION_DIR / name)

        for expect in (False, True):
            print()
            print(f"universe PRESENT, GATE1_EXPECT_UNIVERSE={'1' if expect else 'unset'}")
            out, verdict = run_preflight(expect_universe=expect)
            check("the universe check runs and passes",
                  universe_check_status(verdict) == "OK", verdict or "no line")
            # The ten downstream checks must ACTUALLY have run. If the block
            # were skipped they would simply be absent, and their absence
            # would look identical to success in a summary that only counts
            # failures.
            for downstream in ("symbol count measured", "built by canonical builder",
                               "expiry roles resolved", "universe built today",
                               "universe matches its manifest hash",
                               "spot provenance recorded"):
                check(f"downstream check ran: {downstream}",
                      downstream in out,
                      "" if downstream in out else "the block was skipped")

        # ---- 4. NEGATIVE CONTROL -----------------------------------------
        # A present-but-corrupt universe must FAIL in the phase where absence
        # is tolerated. If it does not, the tolerance has become a hole.
        print()
        print("universe PRESENT BUT TAMPERED, no GATE1_EXPECT_UNIVERSE (negative control)")
        uni = json.loads((SESSION_DIR / "universe.json").read_text())
        uni["spot_source"] = "DRY RUN placeholder"
        (SESSION_DIR / "universe.json").write_text(json.dumps(uni))
        out, _ = run_preflight(expect_universe=False)
        blocked = any("spot is a real observation" in l for l in out.splitlines()
                      if l.strip().startswith("- "))
        check("a tampered universe is caught in the tolerant phase", blocked,
              "presence is validated even when absence would have been tolerated")

finally:
    shutil.rmtree(SESSION_DIR, ignore_errors=True)

print()
if failures:
    print(f"PREFLIGHT ORDERING SELF TEST FAILED -- {len(failures)} issue(s)")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("PREFLIGHT ORDERING SELF TEST PASSED")
sys.exit(0)
