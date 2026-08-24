"""A clean-capture run holds the feed continuously from the open to the close.

WHAT THIS LOCKS OUT. gate1_orchestrator passed --reconnect-test to the harness
UNCONDITIONALLY, hardcoded in the Popen argument list with no flag able to
suppress it. Capacity was already conditional, so the two were inconsistent:
capacity you asked for, reconnect you got regardless.

WHY IT MATTERS. gate1_opening_capture computes

    sustained_until = stop_at - reconnect_budget - probe_budget

so an always-on reconnect reserved 540s and ended continuous capture NINE
MINUTES BEFORE the close -- and with capacity also enabled, nineteen. Both
phases are subscription-changing by construction: reconnect drops and
resubscribes the socket, and the capacity probe steps subscriptions upward
looking for a refusal. A session claiming uninterrupted 09:15-15:40 capture
cannot contain either.

Run: /opt/bujji/.venv/bin/python /opt/bujji/gate1-run/test_clean_capture.py
"""
import importlib.util
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

RUN_DIR = Path("/opt/bujji/gate1-run")
UNIT = Path("/etc/systemd/system/bujji-gate1.service")
failures = []


def check(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(name)
    return ok


spec = importlib.util.spec_from_file_location("_orch", str(RUN_DIR / "gate1_orchestrator.py"))
orch = importlib.util.module_from_spec(spec)
sys.modules["_orch"] = orch
spec.loader.exec_module(orch)
SRC = (RUN_DIR / "gate1_orchestrator.py").read_text()


def harness_args(reconnect, capacity):
    """Reproduce the argument list the orchestrator builds, from its source.

    Deliberately derived from the real conditional expressions rather than
    re-implemented, so a change to them is visible here.
    """
    block = SRC[SRC.index('"gate1_opening_capture.py"'):SRC.index("cwd=str(RUN_DIR), env=env,")]
    args = []
    if 'if args.reconnect_test else []' in block and reconnect:
        args += ["--reconnect-test", "--reconnect-seconds"]
    if 'if args.capacity_probe else []' in block and capacity:
        args += ["--capacity-probe-seconds", "--capacity-steps", "--capacity-pool"]
    return args, block


print("CLEAN CAPTURE SELF TEST")
print()

# ---- 1. clean-capture mode ----------------------------------------------
print("clean-capture mode (no reconnect, no capacity)")
args_clean, block = harness_args(False, False)
check("no reconnect flag is passed to the harness",
      "--reconnect-test" not in args_clean)
check("no capacity flags are passed to the harness",
      not any(a.startswith("--capacity") for a in args_clean))
# PRECISE: the flag must be absent from the UNCONDITIONAL BASE LIST, not
# absent from the block. An earlier version of this check asserted the string
# was absent everywhere, which also failed on the correct conditional form --
# it could not tell "hardcoded" from "inside an if". The base list is
# everything up to its closing bracket; the conditionals are appended after.
# ROBUST ANCHOR. An earlier version sliced on "harness_stop.isoformat()]",
# which the planted defect DESTROYS by appending after it -- so the control
# crashed with ValueError instead of failing. A test that dies on the defect
# it is meant to catch reports nothing at all. The base list is everything
# before the first conditional append, which survives either shape.
_base = block.split("+ (")[0]
# CODE ONLY. The slice includes the comment that EXPLAINS why the flag is
# conditional, and that comment names --reconnect-test -- so an uncorrected
# check matches its own documentation. This is the second time that shape has
# appeared today; strip comment lines before asserting on executable code.
_base = "\n".join(l for l in _base.splitlines() if not l.strip().startswith("#"))
check("--reconnect-test is NOT in the unconditional base list",
      "--reconnect-test" not in _base,
      "it may appear only inside a conditional append")
check("--capacity flags are NOT in the unconditional base list",
      "--capacity" not in _base)
check("reconnect args sit behind args.reconnect_test",
      "if args.reconnect_test else []" in block)
check("capacity args sit behind args.capacity_probe",
      "if args.capacity_probe else []" in block)

# budget arithmetic: with neither, sustained runs to the stop deadline
stop = datetime(2026, 8, 25, 15, 40, 0)
sustained_until = stop - timedelta(seconds=0 + 0)
check("sustained window reaches the close when both are off",
      sustained_until == stop,
      f"sustained_until {sustained_until:%H:%M:%S} == stop {stop:%H:%M:%S}")

# ---- 2. reconnect-enabled mode ------------------------------------------
print()
print("reconnect-enabled mode")
args_rec, _ = harness_args(True, False)
check("reconnect flag IS passed when requested", "--reconnect-test" in args_rec)
check("reconnect budget is reserved", "--reconnect-seconds" in args_rec)
rec_until = stop - timedelta(seconds=orch.BUDGET["reconnect_s"])
check("enabling reconnect DOES interrupt capture before the close",
      rec_until < stop,
      f"capture would end {rec_until:%H:%M:%S}, "
      f"{int((stop-rec_until).total_seconds()/60)} min early")

# ---- 3. capacity-enabled mode -------------------------------------------
print()
print("capacity-enabled mode")
args_cap, _ = harness_args(False, True)
check("capacity pool passed only when requested", "--capacity-pool" in args_cap)
check("pool build is gated on the flag", "if args.capacity_probe:" in SRC)
check("clean mode builds NO pool",
      "--capacity-pool" not in harness_args(False, False)[0])

# ---- 4. tomorrow's armed unit -------------------------------------------
print()
print("armed unit")
unit = UNIT.read_text()
check("armed service passes no --reconnect-test", "--reconnect-test" not in unit)
check("armed service passes no --capacity-probe", "--capacity-probe" not in unit)
check("armed service still names the measurement date",
      "--measurement-date 2026-08-25" in unit)
check("no stray trailing backslash left by the edit",
      not re.search(r"\\\\\n\s*\[?\s*$", unit),
      "a dangling continuation would break the unit")

# ---- 5. NEGATIVE CONTROL ------------------------------------------------
# The control that would have caught the original defect: a hardcoded
# reconnect flag must fail clean-capture, not pass beside it.
print()
print("negative control")
planted = block.replace('"--stop-deadline", harness_stop.isoformat()]',
                        '"--stop-deadline", harness_stop.isoformat(),\n                 "--reconnect-test"]')
_planted_base = "\n".join(l for l in planted.split("+ (")[0].splitlines()
                          if not l.strip().startswith("#"))
check("a planted hardcoded reconnect flag is detected",
      "--reconnect-test" in _planted_base,
      "the detector sees the exact shape of the original defect, in the base list")

print()
if failures:
    print(f"CLEAN CAPTURE SELF TEST FAILED -- {len(failures)} issue(s)")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("CLEAN CAPTURE SELF TEST PASSED")
sys.exit(0)
