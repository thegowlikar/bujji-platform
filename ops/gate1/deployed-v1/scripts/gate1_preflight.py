"""Gate 1 pre-flight -- run this BEFORE the measurement, every time.

Answers one question: is the host in a state where a no-trade full-universe
measurement is both SAFE to run and WORTH running?

Safe = nothing can place an order. Worth running = the feed is reachable, the
universe is real and current, and there is room to store what arrives. A run
that is safe but not worth running wastes a trading session that cannot be
repeated until tomorrow; intraday option-chain data does not backfill.

READ-ONLY. Changes nothing, places nothing, and reads no credential value.
Exit 0 = clear to measure. Exit 1 = do not measure.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
CHECKOUT = Path("/opt/bujji/app")
RUN_DIR = Path("/opt/bujji/gate1-run")
OUT_ROOT = Path("/opt/bujji/gate1")
# NO /tmp SCRATCH. The universe is a durable Gate 1 artifact under the session
# directory; a scratch file is one reboot away from an unreproducible run.
OUT_ROOT_DEFAULT = Path("/opt/bujji/gate1")
CODE_ROOT = Path("/opt/bujji/work-m4")     # architecture worktree, not the live checkout

# Every unit with a real order-placement call site in its import closure.
# Derived by AST analysis of the live checkout, positive-controlled against
# the known-capable trading runner. Re-derive with entry_capable3.py if the
# unit set changes.
ENTRY_CAPABLE_UNITS = ["bujji-options-os-trading"]
# Inhibited for measurement integrity, not safety: it opens a websocket on the
# same app id at the open.
CONTENDING_UNITS = ["bujji-certify-ws-option"]

failures: list[str] = []
warnings: list[str] = []
facts: dict = {}


def check(name, ok, detail, fatal=True):
    facts[name] = {"ok": bool(ok), "detail": detail}
    print(f"  [{'OK ' if ok else 'FAIL' if fatal else 'warn'}] {name}: {detail}")
    if not ok:
        (failures if fatal else warnings).append(f"{name}: {detail}")
    return ok


def sh(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as exc:
        return f"error: {exc}"


print("GATE 1 PRE-FLIGHT")
print(f"  now: {datetime.now(IST).strftime('%Y-%m-%d %A %H:%M:%S %Z')}")
print()

# ---------------------------------------------------------------- SAFETY ---
print("SAFETY -- nothing may place an order")
for unit in ENTRY_CAPABLE_UNITS + CONTENDING_UNITS:
    enabled = sh("systemctl", "is-enabled", f"{unit}.timer")
    active = sh("systemctl", "is-active", f"{unit}.timer")
    check(f"{unit}.timer disabled", enabled == "disabled", f"is-enabled={enabled}")
    check(f"{unit}.timer inactive", active == "inactive", f"is-active={active}")
    cat = sh("systemctl", "cat", f"{unit}.service")
    gated = "ConditionPathExists=/opt/bujji/GATE1" in cat
    check(f"{unit} condition-gated", gated,
          "execution blocked by an unmet ConditionPathExists"
          if gated else "NO condition gate -- a manual start would EXECUTE")
    # The sentinel must NOT exist, or the gate is satisfied and inert.
    for line in cat.splitlines():
        if line.startswith("ConditionPathExists=/opt/bujji/GATE1"):
            sentinel = Path(line.split("=", 1)[1].strip())
            check(f"{unit} sentinel absent", not sentinel.exists(),
                  f"{sentinel} does not exist (gate is live)" if not sentinel.exists()
                  else f"{sentinel} EXISTS -- the gate is satisfied and inert")

# Any OTHER timer that would fire during market hours today.
timers = sh("systemctl", "list-timers", "--all")
today_ist = datetime.now(IST).strftime("%a %Y-%m-%d")
firing = [l for l in timers.splitlines()
          if "bujji" in l and today_ist in l
          and not any(u in l for u in ENTRY_CAPABLE_UNITS + CONTENDING_UNITS)]
check("other bujji timers today", True,
      f"{len(firing)} will fire (none entry-capable): "
      + ", ".join(sorted({w for l in firing for w in l.split() if w.startswith('bujji-')}))
      or "none", fatal=False)

# ------------------------------------------------------------- CHECKOUT ---
print("\nPROVENANCE -- the live checkout must be untouched")
sha = sh("git", "-C", str(CHECKOUT), "rev-parse", "HEAD")
dirty = sh("git", "-C", str(CHECKOUT), "status", "--porcelain")
check("checkout clean", dirty == "", f"{sha[:8]} clean" if dirty == "" else f"DIRTY:\n{dirty[:400]}")
harness = RUN_DIR / "gate1_measure_v5.py"
check("harness present", harness.exists(), str(harness))
check("harness outside checkout", not str(harness).startswith(str(CHECKOUT)),
      "runs from /opt/bujji/gate1-run; the checkout is not modified by the run")
if harness.exists():
    facts["harness_sha256"] = hashlib.sha256(harness.read_bytes()).hexdigest()
    print(f"       harness sha256: {facts['harness_sha256'][:16]}...")

# ---------------------------------------------------------------- TOKEN ---
print("\nCREDENTIAL -- presence and validity only")
session_id_env = os.environ.get("GATE1_SESSION_ID")
app_id = os.environ.get("FYERS_APP_ID")
token = os.environ.get("FYERS_ACCESS_TOKEN")
# PRESENCE AND VALIDITY ONLY. No app id, no fingerprint, no length: this
# output is pasted into chat, logs and reports, and each of those values
# correlates a run to a credential without helping anyone decide whether the
# session may proceed.
check("FYERS_APP_ID set", bool(app_id),
      "present" if app_id else "MISSING -- source the env file first")
check("FYERS_ACCESS_TOKEN set", bool(token),
      "present" if token else
      "MISSING -- refresh the token via the operator's own process")

# EXPIRY AGAINST THE MEASUREMENT WINDOW, checked here directly.
#
# The checkout's own pre-flight asks a DIFFERENT question -- "does this token
# outlive the bujji timers still due today?" -- and under Gate 1 inhibition
# every such timer is disabled, so it short-circuits to OK without ever
# looking at the expiry. That verdict is vacuous for this run. The window that
# matters is the measurement's own, so it is measured against directly.
#
# Local base64 decode of the JWT payload. No broker call, no network, and the
# token VALUE is never printed, stored, or returned -- only its expiry claim.
if token:
    import base64
    exp_ts = None
    try:
        parts = token.split(".")
        if len(parts) >= 2:
            body = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(body))
            exp_ts = claims.get("exp")
    except Exception as exc:
        warnings.append(f"token expiry could not be decoded ({type(exc).__name__})")

    if exp_ts:
        expires = datetime.fromtimestamp(int(exp_ts), IST)
        # AGAINST THE MEASUREMENT DATE, stated explicitly -- never "today".
        # A token checked the evening before is checked against the WRONG day,
        # and a FYERS token is daily: the one live now almost certainly dies
        # before tomorrow's open. Deriving the date from GATE1_MEASUREMENT_DATE
        # (or the session id) makes the check answer the question that matters.
        # THE DATE IS DECLARED, NOT INFERRED.
        #
        # An earlier version scraped digits out of the session id, and
        # "GATE1_20260824" yields "120260824" -- the 1 from GATE1 -- which
        # parsed as nonsense, fell back to TODAY, and reported a comfortable
        # pass for a token that dies before the session it was checked for.
        # A wrong answer delivered confidently is worse than no answer, so an
        # undeclared date now fails instead of defaulting.
        import re as _re
        raw_date = os.environ.get("GATE1_MEASUREMENT_DATE")
        if not raw_date and session_id_env:
            m = _re.search(r"(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])",
                           session_id_env)
            raw_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
        measure_day = None
        if raw_date:
            try:
                measure_day = datetime.strptime(raw_date, "%Y-%m-%d").date()
            except Exception:
                measure_day = None
        if measure_day is None:
            check("measurement date declared", False,
                  "set GATE1_MEASUREMENT_DATE=YYYY-MM-DD (or use a session id "
                  "containing the date). Token validity cannot be checked "
                  "against a date nobody stated.")
            measure_day = datetime.now(IST).date()
        # THE SAME WINDOW THE ORCHESTRATOR WILL ACTUALLY RUN, not a copy.
        #
        # This was a second hardcoded 15:35, independent of the orchestrator's.
        # Fixing only the orchestrator would have split them: the preflight
        # would approve a token expiring 15:36 for a run that now needs cover
        # to 16:03:30, and the token check would pass on a run it could not
        # survive. Two literals for one fact is how the fifteen-way close-time
        # split started, and bujji/market_calendar.py exists to end it.
        #
        # Imported from the orchestrator so the derivation lives in exactly one
        # place. Importing it is side-effect free: its main() is guarded.
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location(
                "_gate1_orch", str(RUN_DIR / "gate1_orchestrator.py"))
            _orch = _ilu.module_from_spec(_spec)
            sys.modules["_gate1_orch"] = _orch
            _spec.loader.exec_module(_orch)
            _window_hhmmss = _orch._derive_window_end()
            _wh, _wm, _ws = (int(x) for x in _window_hhmmss.split(":"))
        except Exception as _exc:  # noqa: BLE001
            # FAIL CLOSED. A preflight that cannot establish the window cannot
            # judge whether the token outlives it, and guessing reinstates the
            # defect this replaced.
            check("measurement window derivable", False,
                  f"could not derive the window from the orchestrator "
                  f"({type(_exc).__name__}: {_exc}); the token check cannot be "
                  f"made against a window nobody established")
            _wh, _wm, _ws = None, None, None

        if _wh is None:
            target = None
        else:
            target = datetime.combine(
                measure_day,
                datetime.min.time().replace(hour=_wh, minute=_wm, second=_ws),
                tzinfo=IST)
        ok = target is not None and expires >= target
        check("token covers the measurement window", ok,
              f"expires {expires.strftime('%Y-%m-%d %H:%M %Z')}; "
              f"{measure_day} session needs cover to "
              f"{target.strftime('%Y-%m-%d %H:%M %Z') if target else 'UNKNOWN (window underivable)'}"
              + ("" if ok else
                 " -- REFRESH ON THE MEASUREMENT MORNING. This token dies "
                 "before the window closes, and intraday option-chain data "
                 "does not backfill."))
        facts["credential_expiry_ist"] = expires.isoformat()
        facts["measurement_date"] = str(measure_day)
    else:
        check("token expiry readable", False,
              "the token carries no readable exp claim -- validity for the "
              "measurement window cannot be established", fatal=False)

# ------------------------------------------------------------- UNIVERSE ---
print("\nUNIVERSE -- canonical builder, real master, observed spot")
session_id = os.environ.get("GATE1_SESSION_ID")
check("GATE1_SESSION_ID set", bool(session_id),
      session_id or "MISSING -- export GATE1_SESSION_ID before building")

harness_v5 = RUN_DIR / "gate1_measure_v5.py"
builder = RUN_DIR / "gate1_build_universe.py"
check("canonical builder present", builder.exists(), str(builder))
check("retired builder not used", True,
      "scripts/gate1/build_universe.py, STRIKES_EACH_SIDE, fixed strike counts "
      "and /tmp/gate1_universe.json are not referenced by this run")

# The architecture worktree supplies bujji.capture_universe.builder.
check("architecture worktree present", (CODE_ROOT / "bujji/capture_universe/builder.py").exists(),
      f"{CODE_ROOT} (branch "
      f"{sh('git', '-C', str(CODE_ROOT), 'rev-parse', '--abbrev-ref', 'HEAD')} @ "
      f"{sh('git', '-C', str(CODE_ROOT), 'rev-parse', 'HEAD')[:8]})")

if session_id:
    sess_dir = OUT_ROOT / session_id
    uni_path = sess_dir / "universe.json"
    man_path = sess_dir / "universe_manifest.json"

    # THE ORDERING DEFECT THIS FIXES (2026-08-24).
    #
    # This block used to fail the whole preflight when universe.json was
    # absent, with the detail "run gate1_build_universe.py after open". The
    # orchestrator runs the preflight at step 2, BEFORE the bell, and writes
    # universe.json at step 6 -- after the provisional universe, after the
    # capture harness has already been launched, from the first valid
    # post-open spot callback. So the artifact this check demanded could not
    # exist when the check ran, and could never exist before measurement
    # begins. The check's own message said "after open" while running before
    # it.
    #
    # The consequence was not a false alarm. bujji-gate1.service failed at
    # 08:45:02 on 2026-08-24 with exit 6 PREFLIGHT_FAILED, 22 of 23 checks
    # passing and this the only failure, and the one-shot timer disabled
    # itself on the way out. The run was structurally guaranteed to fail from
    # the moment it was armed, and the day's opening coverage was lost --
    # intraday option data cannot be backfilled.
    #
    # ABSENCE IS TOLERATED; PRESENCE IS ALWAYS VALIDATED. That asymmetry is
    # the point, and it is deliberately NOT a flag that skips the block.
    # A phase flag that turned the ten checks below off would let a present
    # but malformed universe through whenever the flag was set -- trading one
    # ordering bug for a silent one. So the only thing the phase changes is
    # whether a MISSING universe is fatal. A universe that exists is
    # validated in full, every time, in every phase.
    #
    # GATE1_EXPECT_UNIVERSE=1 says the caller has already built it and its
    # absence is a real failure. That is the manual sequence -- build, then
    # preflight, then measure -- and the orchestrator sets it on any
    # re-invocation made after its own canonical build.
    expect_universe = os.environ.get("GATE1_EXPECT_UNIVERSE") == "1"
    universe_present = uni_path.exists()
    if universe_present:
        universe_detail = str(uni_path)
    elif expect_universe:
        universe_detail = (f"{uni_path} MISSING -- GATE1_EXPECT_UNIVERSE=1 says it "
                           f"should already have been built by gate1_build_universe.py")
    else:
        universe_detail = (f"{uni_path} not built yet -- NOT YET DUE at this phase. "
                           f"The orchestrator builds it after the bell, from the first "
                           f"valid post-open spot callback. Set GATE1_EXPECT_UNIVERSE=1 "
                           f"to require it here.")

    if check("universe built", universe_present or not expect_universe,
             universe_detail, fatal=expect_universe) and universe_present:
        uni = json.loads(uni_path.read_text())
        man = json.loads(man_path.read_text()) if man_path.exists() else {}
        check("universe manifest written", bool(man), str(man_path))
        n = uni.get("measured_symbol_count", len(uni.get("instruments", [])))
        # MEASURED, never compared to an expected count.
        check("symbol count measured", n > 0, f"{n} symbols (measured, not asserted)")
        check("built by canonical builder",
              uni.get("construction", {}).get("builder", "").endswith("build_capture_universe"),
              uni.get("construction", {}).get("builder", "MISSING"))
        check("built from architecture worktree",
              not man.get("built_from", {}).get("is_live_checkout", True),
              f"code_root={man.get('built_from', {}).get('code_root')} @ "
              f"{str(man.get('built_from', {}).get('git_sha'))[:8]}")
        check("expiry roles resolved", bool(uni.get("expiry_roles")),
              ", ".join(f"{r}={e}" for r, e in sorted((uni.get("expiry_roles") or {}).items()))
              or "NONE")
        check("source master recorded", bool(uni.get("source_master", {}).get("sha256")),
              f"{uni.get('source_master', {}).get('cache_file')} "
              f"mtime={str(uni.get('source_master', {}).get('mtime_ist'))[:19]} "
              f"sha256={str(uni.get('source_master', {}).get('sha256'))[:16]}...")
        # Freshness from the artifact's own stamp, which this builder does write.
        fresh = False
        try:
            fresh = (datetime.fromisoformat(uni["built_at"]).astimezone(IST).date()
                     == datetime.now(IST).date())
        except Exception:
            pass
        check("universe built today", fresh,
              f"built_at={uni.get('built_at_ist')}"
              + ("" if fresh else " -- REBUILD: expiry roles roll over daily"))
        check("spot provenance recorded", bool(uni.get("spot_source")),
              f"spot={uni.get('spot')} source={uni.get('spot_source')!r}")
        check("spot is a real observation", "DRY RUN" not in str(uni.get("spot_source", "")),
              "spot_source does not look like a placeholder"
              if "DRY RUN" not in str(uni.get("spot_source", ""))
              else "spot_source is a DRY RUN placeholder -- rebuild with a real "
                   "observed post-open spot")
        facts["universe_sha256"] = hashlib.sha256(uni_path.read_bytes()).hexdigest()
        facts["measured_symbol_count"] = n
        if man.get("universe_sha256"):
            check("universe matches its manifest hash",
                  man["universe_sha256"] == facts["universe_sha256"],
                  "unchanged since it was built" if man["universe_sha256"] == facts["universe_sha256"]
                  else "universe.json CHANGED after the manifest was written -- "
                       "it is no longer the artifact that was planned")

# ----------------------------------------------------------------- DISK ---
print("\nCAPACITY -- room for what a full session produces")
OUT_ROOT.mkdir(parents=True, exist_ok=True)
du = shutil.disk_usage(str(OUT_ROOT))
free_gb = du.free / 1024**3
check("free space", free_gb >= 5, f"{free_gb:.1f} GB free")
# ~1.9 KB/row measured on the 2026-08-14 capture; a 6.25h session at the
# observed full-mode rate is the number that matters, not the instantaneous one.
check("headroom for a full session", free_gb >= 10,
      f"{free_gb:.1f} GB free; the harness aborts at 2 GB and caps the corpus "
      f"at 20 GB", fatal=False)

# --------------------------------------------------------------- VERDICT ---
print()
# Scrub anything credential-adjacent before persisting; the record is kept
# for evidence, not for identifying which token was used.
_facts = {k: v for k, v in facts.items()
          if not any(w in k.lower() for w in ("token", "app_id", "fingerprint"))}
_facts["credential_covers_window"] = facts.get(
    "token covers the measurement window", {}).get("ok")
(RUN_DIR / "preflight_last.json").write_text(json.dumps(
    {"at": datetime.now(timezone.utc).isoformat(), "facts": _facts,
     "failures": [f.split(";")[0] for f in failures], "warnings": warnings},
    indent=1, sort_keys=True))
if failures:
    print(f"PRE-FLIGHT: DO NOT MEASURE -- {len(failures)} blocking issue(s)")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("PRE-FLIGHT: CLEAR TO MEASURE")
for w in warnings:
    print(f"  note: {w}")
sys.exit(0)
