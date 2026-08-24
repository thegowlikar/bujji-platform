"""Prove the Gate 1 service can do its job INSIDE its own systemd sandbox.

Every check below is a capability the unattended run depends on and that a
sandbox can silently remove: ProtectSystem makes paths read-only,
ProtectHome hides directories, ReadWritePaths is an allow-list, and none of
that shows up when the same script passes in an interactive root shell.

Touches no market, places nothing, and changes no trading configuration.
Exits 0 only if every capability holds.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
RUN_DIR = Path("/opt/bujji/gate1-run")
OUT_ROOT = Path("/opt/bujji/gate1")
ENV_FILE = Path("/opt/bujji/.env")
SESSION = "SANDBOX_SELFTEST"
results = []


def _future(minutes: int) -> str:
    from datetime import timedelta
    return (datetime.now(IST) + timedelta(minutes=minutes)).strftime("%H:%M:%S")


def check(name, ok, detail):
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}: {detail}", flush=True)
    return ok


print("GATE 1 SERVICE-SANDBOX SELF TEST")
print(f"  uid={os.getuid()} cwd={os.getcwd()}")
print(f"  at {datetime.now(IST):%Y-%m-%d %H:%M:%S %Z}\n")

# --- 1. credential location readable, contents never exposed --------------
readable = False
try:
    with open(ENV_FILE, "r") as fh:
        has_token = any(l.startswith("FYERS_ACCESS_TOKEN=") for l in fh)
    readable = True
except Exception as exc:
    has_token = False
    detail = f"{type(exc).__name__}"
check("credential location readable", readable and has_token,
      "the canonical credential file is readable and carries a token key; "
      "no value was read into any variable, logged, or returned"
      if readable and has_token else "NOT readable under this sandbox")

# --- 2. run lock acquire + release ----------------------------------------
import fcntl
lock_ok = False
lock_path = RUN_DIR / ".gate1.selftest.lock"
try:
    fh = open(lock_path, "w")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(fh, fcntl.LOCK_UN)
    fh.close()
    lock_path.unlink(missing_ok=True)
    lock_ok = True
except Exception as exc:
    lock_ok = False
check("run lock acquire/release", lock_ok,
      f"flock taken and released under {RUN_DIR}"
      if lock_ok else "could not lock in the run directory")

# --- 3. durable session directory ------------------------------------------
sess = OUT_ROOT / SESSION
dir_ok = False
try:
    sess.mkdir(parents=True, exist_ok=True)
    dir_ok = sess.is_dir()
except Exception as exc:
    dir_ok = False
check("durable output directory", dir_ok, str(sess))

# --- 4. sealed artifact + operator summary --------------------------------
seal_ok = False
try:
    import hashlib
    art = sess / "selftest_artifact.jsonl"
    art.write_text('{"synthetic":true,"note":"sandbox self test"}\n')
    digest = hashlib.sha256(art.read_bytes()).hexdigest()
    seal = sess / "selftest_seal.json"
    tmp = seal.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"artifact": art.name, "sha256": digest,
                               "bytes": art.stat().st_size}, indent=1))
    tmp.replace(seal)                      # atomic, same as the real path
    seal_ok = seal.exists() and json.loads(seal.read_text())["sha256"] == digest
except Exception as exc:
    seal_ok = False
check("sealed artifact write (atomic)", seal_ok,
      "artifact written and sha256-sealed via tmp+rename"
      if seal_ok else "could not write a sealed artifact")

# --- 5. the orchestrator dry-run path, in-sandbox --------------------------
orch_ok, orch_detail = False, ""
try:
    r = subprocess.run(
        ["/opt/bujji/.venv/bin/python", str(RUN_DIR / "gate1_orchestrator.py"),
         "--session-id", SESSION + "_ORCH", "--measurement-date",
         datetime.now(IST).date().isoformat(),
         "--dry-run", "mock_pass", "--window-end", "23:59",
         "--token-deadline", "23:58",
         # Future pre-open deadlines, so the case under test is the
         # orchestrator path rather than the pre-open gate firing first.
         "--subscribe-by", _future(30), "--open-at", _future(31)],
        capture_output=True, text=True, timeout=300, cwd=str(RUN_DIR))
    summary = OUT_ROOT / (SESSION + "_ORCH") / "gate1_run_summary.json"
    doc = json.loads(summary.read_text())
    # Exit 10 = GATE1_FAIL, which is CORRECT for a mocked harness: it writes no
    # results file, so the verdict is UNKNOWN, and absence of evidence is never
    # a PASS. What is being proved here is that the whole path RAN in-sandbox.
    orch_ok = r.returncode == 10 and doc["result_category"] == "GATE1_FAIL"
    orch_detail = (f"token->preflight->universe->harness path executed; "
                   f"exit={r.returncode} category={doc['result_category']}")
except Exception as exc:
    orch_detail = f"{type(exc).__name__}: {exc}"
check("orchestrator dry-run path in-sandbox", orch_ok, orch_detail)

# --- 6. credential-free ntfy ----------------------------------------------
ntfy_ok = False
try:
    n = subprocess.run(["/opt/bujji/notify.sh",
                        "Bujji Gate 1: sandbox self test",
                        "Automation self test running under the service "
                        "sandbox. No measurement, no trading. Ignore.",
                        "low", "test_tube"],
                       capture_output=True, text=True, timeout=30)
    ntfy_ok = n.returncode == 0
    # The body above is a fixed string. No credential-adjacent value can reach
    # it, because none is ever read into this process.
except Exception:
    ntfy_ok = False
check("credential-free ntfy emitted", ntfy_ok,
      "notification sent with a fixed, credential-free body"
      if ntfy_ok else "notify.sh returned non-zero (delivery is best-effort)")

# --- 7. the sandbox is actually a sandbox ---------------------------------
# POSITIVE CONTROL. If a write to a path OUTSIDE ReadWritePaths succeeds, then
# ProtectSystem is not in force and none of the checks above prove anything
# about the real service's restrictions.
denied = False
try:
    probe = Path("/usr/lib/gate1-sandbox-probe")
    probe.write_text("x")
    probe.unlink(missing_ok=True)
except Exception:
    denied = True
check("sandbox restrictions in force (control)", denied,
      "a write outside ReadWritePaths was DENIED, so ProtectSystem is real "
      "and these results describe the actual sandbox"
      if denied else "a write to /usr SUCCEEDED -- the sandbox is not applied, "
                     "so every result above is meaningless")

# --- 8. no trading unit was touched ---------------------------------------
states = {}
for unit in ("bujji-options-os-trading", "bujji-certify-ws-option"):
    states[unit] = (
        subprocess.run(["systemctl", "is-enabled", f"{unit}.timer"],
                       capture_output=True, text=True).stdout.strip(),
        subprocess.run(["systemctl", "is-active", f"{unit}.timer"],
                       capture_output=True, text=True).stdout.strip())
untouched = all(v == ("disabled", "inactive") for v in states.values())
check("trading isolation unchanged", untouched, json.dumps(states))

# --- summary ---------------------------------------------------------------
sess.mkdir(parents=True, exist_ok=True)
out = sess / "sandbox_selftest_result.json"
tmp = out.with_suffix(".json.tmp")
tmp.write_text(json.dumps(
    {"at": datetime.now(IST).isoformat(), "uid": os.getuid(),
     "cwd": os.getcwd(), "checks": results,
     "all_passed": all(c["ok"] for c in results)}, indent=1, sort_keys=True))
tmp.replace(out)

failed = [c["check"] for c in results if not c["ok"]]
print()
if failed:
    print(f"SANDBOX SELF TEST FAILED: {failed}")
    sys.exit(1)
print(f"SANDBOX SELF TEST PASSED ({len(results)} checks)")
print(f"  result: {out}")
sys.exit(0)
