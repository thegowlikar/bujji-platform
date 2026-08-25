"""measure_v5 must stop on the clock and seal on every terminal path.

WHAT THIS LOCKS OUT, measured on 2026-08-24:

  1. run_phase() called guard() ONCE at phase entry, then
     time.sleep(seconds) -- 18,000 seconds for the sustained phase. A 15:35
     stop deadline could not fire mid-phase, so the run continued to 20:26
     and accumulated 4h46m of post-close silence.
  2. The harness was launched --mode both --sustained-minutes 300. That value
     applies PER MODE: 5h of lite plus 5h of full, inside a 6-hour session.
     Nothing checked feasibility, so lite consumed the day and full mode
     entered sustained with 14 minutes of market left.
  3. There was no signal handler, no finally and no atexit. When the operator
     terminated the run, sealing never ran: no offline re-parse, no accounting
     identity, no verdict. 394 MB of good evidence with nothing certifying it.

Run: /opt/bujji/.venv/bin/python /opt/bujji/gate1-run/test_measure_v5_deadline.py
"""
import importlib.util
import sys
import types
from pathlib import Path

HARNESS = Path("/opt/bujji/gate1-run/gate1_measure_v5.py")
failures = []


def check(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(name)
    return ok


spec = importlib.util.spec_from_file_location("_mv5", str(HARNESS))
mv5 = importlib.util.module_from_spec(spec)
sys.modules["_mv5"] = mv5
spec.loader.exec_module(mv5)

SRC = HARNESS.read_text()

print("MEASURE_V5 DEADLINE / SEALING SELF TEST")
print()

# ---- 1. combined-mode budget overflow -----------------------------------
print("plan feasibility")


def _args(**kw):
    a = types.SimpleNamespace(
        mode="both", phase_seconds=300, sustained_minutes=300,
        stall_test_minutes=0, reconnect_test=False)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


ramp = mv5.derive_ramp(249)
check("derive_ramp(249) has the expected rungs", ramp == [4, 24, 62, 124, 186, 249],
      str(ramp))

plan = mv5._plan_seconds(_args(), ramp)
# 6 rungs x 300s = 1800, + 300m = 18000 -> 19800 per mode, x2 modes = 39600
check("mode=both counts BOTH modes", plan["total_s"] == 19800 * 2,
      f"{plan['total_s']}s = {plan['per_mode_s']}s x 2")
check("today's actual launch is over a 6h session",
      plan["total_s"] > 6 * 3600,
      f"{plan['total_s']}s planned vs 21600s in a session -- "
      f"over by {(plan['total_s'] - 6*3600)/3600:.1f}h")

single = mv5._plan_seconds(_args(mode="full"), ramp)
check("mode=full counts one mode", single["total_s"] == 19800, f"{single['total_s']}s")
check("a single-mode plan fits a 6h session", single["total_s"] < 6 * 3600)

check("reconnect budget is counted when enabled",
      mv5._plan_seconds(_args(reconnect_test=True), ramp)["total_s"]
      > plan["total_s"])
check("reconnect budget is ZERO when disabled",
      mv5._plan_seconds(_args(reconnect_test=False), ramp)["total_s"]
      == plan["total_s"])

# ---- 2. the refusal happens BEFORE a socket exists -----------------------
print()
print("refusal ordering")
i_plan = SRC.index("INFEASIBLE PLAN")
i_sock = SRC.index("sock = data_ws.FyersDataSocket(")
check("the infeasible-plan refusal precedes socket construction", i_plan < i_sock,
      "an impossible plan never touches the venue")
i_loop = SRC.index('for mode in (["lite", "full"] if args.mode == "both"')
check("the plan check precedes the mode loop", i_plan < i_loop)

# ---- 3. interruptible waits ---------------------------------------------
print()
print("deadline checks inside waits")
rp = SRC[SRC.index("def run_phase("):SRC.index("for n in ramp:")]
# CODE ONLY, NOT COMMENTS. An earlier version of this check matched the
# substring anywhere in the slice and failed against the comment that
# EXPLAINS the removed sleep -- the test flagged its own documentation as the
# defect. Strip comment lines before asserting on executable code.
_rp_code = "\n".join(l for l in rp.splitlines()
                     if not l.strip().startswith("#"))
check("run_phase no longer sleeps the whole phase in one call",
      "time.sleep(seconds)" not in _rp_code,
      "the single uninterruptible sleep is gone from executable code")
check("run_phase re-checks guard() inside its wait",
      rp.count("guard(corpus)") >= 2,
      f"guard() appears {rp.count('guard(corpus)')}x -- entry plus the loop")
check("the wait is bounded by DEADLINE_POLL_S",
      "DEADLINE_POLL_S" in rp)
check("poll interval is short enough to matter", mv5.DEADLINE_POLL_S <= 30,
      f"{mv5.DEADLINE_POLL_S}s")

# ---- 4. terminal states are mutually exclusive and complete --------------
print()
print("terminal states")
states = {mv5.TERMINAL_COMPLETED, mv5.TERMINAL_DEADLINE,
          mv5.TERMINAL_INTERRUPTED, mv5.TERMINAL_ERROR}
check("four distinct terminal states", len(states) == 4, ", ".join(sorted(states)))
check("SIGTERM is handled", "_install_signal_handlers" in SRC
      and SRC.count("_install_signal_handlers()") >= 1)
check("the handler is actually CALLED, not just defined",
      "\n    _install_signal_handlers()" in SRC,
      "a defined-but-uncalled handler seals nothing")

tail = SRC[SRC.index("_terminal = TERMINAL_COMPLETED"):]
for label, needle in (("SystemExit", "except SystemExit as _exc:"),
                      ("KeyboardInterrupt", "except KeyboardInterrupt:"),
                      ("unhandled exception", "except BaseException as _exc:"),
                      ("finally-close", "finally:")):
    check(f"terminal path handles {label}", needle in tail)

check("a deadline stop is distinguished from an interrupt",
      'TERMINAL_DEADLINE if "STOP DEADLINE" in _msg' in tail,
      "an operator stop and a deadline stop are different facts")
check("the terminal state reaches the results document",
      'results["terminal_state"] = _terminal' in tail)
check("evidence_complete is only true on COMPLETED",
      'results["evidence_complete"] = (_terminal == TERMINAL_COMPLETED)' in tail,
      "a partial corpus must never be certified as a complete measurement")

# ---- 5. sealing is reached after the terminal path ----------------------
print()
print("sealing reachability")
i_term = SRC.index('results["terminal_state"] = _terminal')
i_seal = SRC.index("# ---- G5: offline raw-corpus re-parse validation")
i_write = SRC.index('(out / "gate1_results.json").write_text')
check("sealing follows the terminal path", i_term < i_seal < i_write,
      "every terminal state falls through to re-parse, seal and verdict")

print()
if failures:
    print(f"MEASURE_V5 SELF TEST FAILED -- {len(failures)} issue(s)")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("MEASURE_V5 SELF TEST PASSED")
sys.exit(0)
