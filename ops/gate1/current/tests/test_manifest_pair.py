"""universe.json and universe_manifest.json are ONE evidence pair.

WHAT THIS LOCKS OUT, all three found by audit of the v1 preflight:

  1. THE HASH CHECK WAS CONDITIONAL. `if man.get("universe_sha256")` meant a
     manifest lacking the key skipped the comparison entirely and the run
     PASSED. A validation that only runs when its input happens to be present
     is not a validation.
  2. "CANNOT BE READ" WAS NOT GRADED. json.loads had no try, so a corrupt
     manifest raised an uncaught JSONDecodeError and killed the preflight with
     a traceback rather than a named refusal.
  3. THE PAIR WAS NEVER MATCHED BY NAME. The manifest carries `universe_file`
     but nothing checked it described the file being hashed, so pairing rested
     on the hash alone -- which collapses completely under gap 1.

WHAT THE SYMBOL HASH DOES AND DOES NOT DO. `symbols_sha256` VERIFIES a
candidate symbol set you already hold. It cannot RECONSTRUCT the symbols if
universe.json is lost. The pair still requires both files; these tests refuse
every partial state rather than degrading to a weaker check.

Run: /opt/bujji/.venv/bin/python /opt/bujji/gate1-run/test_manifest_pair.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PREFLIGHT = "/opt/bujji/gate1-run/gate1_preflight.py"
OUT_ROOT = Path("/opt/bujji/gate1")
PY = "/opt/bujji/.venv/bin/python"
SESSION = "GATE1_20260824_PAIR_SELFTEST"
SESSION_DIR = OUT_ROOT / SESSION
REAL = OUT_ROOT / "GATE1_V2_TEST"

failures = []


def check(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(name)
    return ok


def run_preflight():
    env = dict(os.environ)
    env["GATE1_SESSION_ID"] = SESSION
    env["GATE1_MEASUREMENT_DATE"] = "2026-08-24"
    env["GATE1_EXPECT_UNIVERSE"] = "1"
    p = subprocess.run([PY, PREFLIGHT], capture_output=True, text=True,
                       timeout=180, env=env)
    return p.stdout + p.stderr, p.returncode


def named_check(out, name):
    """Return 'OK' / 'FAIL' / None for one rendered check line, by exact name."""
    for line in out.splitlines():
        t = line.strip()
        for status in ("[OK ] ", "[FAIL] ", "[warn] "):
            if t.startswith(status) and t[len(status):].split(":", 1)[0] == name:
                return status.strip("[] ")
    return None


def reset(schema=2):
    shutil.rmtree(SESSION_DIR, ignore_errors=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(REAL / "universe.json", SESSION_DIR / "universe.json")
    man = json.loads((REAL / "universe_manifest.json").read_text())
    man["universe_file"] = str(SESSION_DIR / "universe.json")
    man["universe_sha256"] = hashlib.sha256(
        (SESSION_DIR / "universe.json").read_bytes()).hexdigest()
    if schema == 1:
        man["schema"] = "gate1.universe_manifest/1"
        man.pop("symbols_sha256", None)
        man.pop("symbols_sha256_method", None)
    write_man(man)
    return man


def write_man(man):
    (SESSION_DIR / "universe_manifest.json").write_text(json.dumps(man, indent=1))


print("MANIFEST PAIR SELF TEST")
print()

if not (REAL / "universe.json").exists():
    print("  [skip] no v2 universe available to copy")
    sys.exit(0)

try:
    # ---- baseline: a complete, honest v2 pair must PASS -----------------
    print("intact v2 pair (positive control)")
    reset(schema=2)
    out, _ = run_preflight()
    check("universe matches its manifest hash passes",
          named_check(out, "universe matches its manifest hash") == "OK")
    check("symbol set matches its manifest hash passes",
          named_check(out, "symbol set matches its manifest hash") == "OK")
    check("schema reported supported",
          named_check(out, "manifest schema is supported") == "OK")

    # ---- v1 legacy manifest: valid, but cannot check symbols -----------
    print()
    print("v1 legacy manifest (supported, no symbol hash)")
    reset(schema=1)
    out, _ = run_preflight()
    check("v1 schema is accepted",
          named_check(out, "manifest schema is supported") == "OK")
    check("v1 still verifies the content hash",
          named_check(out, "universe matches its manifest hash") == "OK")
    check("v1 runs NO symbol-set check",
          named_check(out, "symbol set matches its manifest hash") is None,
          "a v1 manifest cannot make that claim, so the check must be absent")

    # ---- every refusal ---------------------------------------------------
    print()
    print("refusals")

    reset(); (SESSION_DIR / "universe_manifest.json").unlink()
    out, _ = run_preflight()
    check("missing manifest refuses",
          named_check(out, "universe manifest written") == "FAIL")

    reset(); (SESSION_DIR / "universe_manifest.json").write_text("{not json")
    out, rc = run_preflight()
    check("corrupt manifest is a NAMED refusal, not a traceback",
          named_check(out, "universe manifest parses") == "FAIL"
          and "Traceback" not in out)

    reset(); (SESSION_DIR / "universe.json").write_text("{not json")
    out, _ = run_preflight()
    check("corrupt universe is a NAMED refusal",
          named_check(out, "universe.json parses") == "FAIL"
          and "Traceback" not in out)

    m = reset(); m["universe_file"] = "/somewhere/else/universe.json"; write_man(m)
    out, _ = run_preflight()
    check("manifest naming a different file refuses",
          named_check(out, "manifest names the universe being validated") == "FAIL")

    m = reset(); m["universe_sha256"] = "0" * 64; write_man(m)
    out, _ = run_preflight()
    check("wrong content hash refuses",
          named_check(out, "universe matches its manifest hash") == "FAIL")

    m = reset(); m["symbols_sha256"] = "0" * 64; write_man(m)
    out, _ = run_preflight()
    check("wrong symbol hash refuses",
          named_check(out, "symbol set matches its manifest hash") == "FAIL")

    # THE GAP THAT USED TO PASS SILENTLY.
    m = reset(); del m["universe_sha256"]; write_man(m)
    out, _ = run_preflight()
    check("MISSING content hash is a FAILURE, not a skipped check",
          named_check(out, "manifest carries every required field") == "FAIL",
          "this is the v1 gap: absent key used to skip validation entirely")

    m = reset(); del m["symbols_sha256"]; write_man(m)
    out, _ = run_preflight()
    check("v2 manifest missing its symbol hash refuses",
          named_check(out, "manifest carries every required field") == "FAIL")

    m = reset(); m["schema"] = "gate1.universe_manifest/99"; write_man(m)
    out, _ = run_preflight()
    check("unknown schema refuses",
          named_check(out, "manifest schema is supported") == "FAIL")

    # duplicate symbol in the universe itself
    reset()
    u = json.loads((SESSION_DIR / "universe.json").read_text())
    u["instruments"].append(dict(u["instruments"][0]))
    (SESSION_DIR / "universe.json").write_text(json.dumps(u))
    m = json.loads((SESSION_DIR / "universe_manifest.json").read_text())
    m["universe_sha256"] = hashlib.sha256(
        (SESSION_DIR / "universe.json").read_bytes()).hexdigest()
    write_man(m)
    out, _ = run_preflight()
    check("duplicate symbol refuses",
          named_check(out, "symbols are unique") == "FAIL")

finally:
    shutil.rmtree(SESSION_DIR, ignore_errors=True)

print()
if failures:
    print(f"MANIFEST PAIR SELF TEST FAILED -- {len(failures)} issue(s)")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("MANIFEST PAIR SELF TEST PASSED")
sys.exit(0)
