"""Gate 1 must not declare its own market close.

WHAT THIS LOCKS OUT. gate1_orchestrator.py defaulted --window-end to 15:35 and
gate1_preflight.py independently hardcoded 15:35 for its token check. Neither
matches a real close: the cash segment closes 15:30, F&O closes 15:40 (NSE
circular 2026-05-30, effective 2026-08-03). Bujji trades NIFTY options, i.e.
F&O.

WHAT IT COST. harness_stop is window_end minus the 1410s sealing tail, so a
15:35 window stopped the capture at 15:11:30 and silently discarded the last
28.5 minutes of the F&O session -- including the close, the busiest window of
the day for options -- while reporting a full session.

bujji/market_calendar.py is the single authority, written because fifteen
files had each invented a close literal and disagreed. Gate 1 was frozen
before it could be reconciled to it, which made these the sixteenth and
seventeenth.

Run: /opt/bujji/.venv/bin/python /opt/bujji/gate1-run/test_close_time_authority.py
"""
import importlib.util
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

RUN_DIR = Path("/opt/bujji/gate1-run")
CODE_ROOT = "/opt/bujji/work-m4"

failures = []


def check(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(name)
    return ok


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


print("GATE 1 CLOSE-TIME AUTHORITY SELF TEST")
print()

sys.path.insert(0, CODE_ROOT)
from bujji.market_calendar import CASH_MARKET_CLOSE, FO_MARKET_CLOSE  # noqa: E402

orch = load("_t_orch", RUN_DIR / "gate1_orchestrator.py")

# ---- 1. the capture must end exactly at the F&O close --------------------
window = orch._derive_window_end()
wh, wm, ws = (int(x) for x in window.split(":"))
harness_stop = (datetime(2000, 1, 1, wh, wm, ws)
                - timedelta(seconds=orch._HARNESS_TAIL_S)).time()

check("capture ends at the F&O close", harness_stop == FO_MARKET_CLOSE,
      f"harness_stop={harness_stop} FO_MARKET_CLOSE={FO_MARKET_CLOSE}")
check("capture does NOT end at the cash close", harness_stop != CASH_MARKET_CLOSE,
      f"{CASH_MARKET_CLOSE} is the cash/index close; Bujji trades F&O")

# ---- 2. it is DERIVED, not a new literal that happens to be right --------
# NEGATIVE CONTROL. Move the authority and the derived window must follow. If
# it does not, the value is hardcoded somewhere and only looks correct today.
import bujji.market_calendar as cal  # noqa: E402
real = cal.FO_MARKET_CLOSE
try:
    from datetime import time as _t
    cal.FO_MARKET_CLOSE = _t(14, 5)
    moved = orch._derive_window_end()
    mh, mm, ms = (int(x) for x in moved.split(":"))
    moved_stop = (datetime(2000, 1, 1, mh, mm, ms)
                  - timedelta(seconds=orch._HARNESS_TAIL_S)).time()
    check("the window follows the authority (negative control)",
          moved_stop == _t(14, 5),
          f"authority moved to 14:05, derived stop {moved_stop}")
finally:
    cal.FO_MARKET_CLOSE = real

# ---- 3. preflight and orchestrator must not disagree ---------------------
# THE SPLIT THIS REPLACED. Two files each holding their own copy of one fact
# is how a token check comes to approve a run it cannot survive.
pre_src = (RUN_DIR / "gate1_preflight.py").read_text()
check("preflight derives the window from the orchestrator",
      "_derive_window_end()" in pre_src,
      "one derivation, used by both")
check("preflight holds no close-time literal of its own",
      not re.search(r"replace\(hour=15,\s*minute=(30|35|40)\)", pre_src),
      "no hour=15, minute=NN literal remains")

# ---- 4. no Gate 1 file may default a close time --------------------------
for f in sorted(RUN_DIR.glob("gate1_*.py")):
    src = f.read_text()
    # Only ARGUMENT DEFAULTS matter here. Prose in comments that explains the
    # closes is exactly what should be written down, and must not fail this.
    bad = re.findall(r'default\s*=\s*"(15:(?:15|20|30|35|40)(?::\d\d)?)"', src)
    check(f"{f.name} defaults no close time", not bad,
          f"found {bad}" if bad else "none")

print()
if failures:
    print(f"CLOSE-TIME AUTHORITY SELF TEST FAILED -- {len(failures)} issue(s)")
    for x in failures:
        print(f"  - {x}")
    sys.exit(1)
print("CLOSE-TIME AUTHORITY SELF TEST PASSED")
sys.exit(0)
