"""Verify one short-deadline sandbox run against all seven requirements.

Reads only artifacts and stub telemetry -- nothing inside the process.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

R = Path("/opt/bujji/gate1/SANDBOXTEST")
failures = []


def ck(name, ok, detail=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(name)
    return ok


try:
    res = json.loads((R / "run" / "gate1_results.json").read_text())
except Exception as exc:
    print(f"  [FAIL] results readable: {type(exc).__name__}")
    sys.exit(1)
tel = json.loads((R / "stub_telemetry.json").read_text())
v = res["gate1_verdict"]

ck("1. deadline reached while in sustained capture",
   any(p.get("phase") == "opening_sustained" for p in res.get("phases", []))
   and res.get("terminal_state") == "DEADLINE_REACHED",
   str(res.get("terminal_state")))

# C2 detector: shutdown must be called EXACTLY once -- not zero (leaked feed),
# not twice (double close).
ck("2. feed shutdown called exactly once", tel.get("close_calls") == 1,
   f"close_calls={tel.get('close_calls')}")

ck("3. output sealed", bool(res.get("artifact_seal")),
   f"{len(res.get('artifact_seal', {}))} sealed entries")
ck("4. replay/accounting validation ran", bool(res.get("validation")),
   str(list(res.get("validation", {}).keys())))
ck("5. terminal result is exactly DEADLINE_REACHED",
   res.get("terminal_state") == "DEADLINE_REACHED")
ck("6. evidence_complete is false", res.get("evidence_complete") is False,
   str(res.get("evidence_complete")))
ck("7. cannot read as completed/passing",
   v["verdict"] != "PASS"
   and any("EVIDENCE_INCOMPLETE" in f for f in v.get("failures", [])),
   v["verdict"])

# C3 detector: NO RECORD MAY POSTDATE THE DEADLINE.
#
# Terminal state alone cannot catch an unbounded wait -- a run that sleeps
# past its deadline and then reports DEADLINE_REACHED looks identical in the
# summary. The corpus is the witness: if a tick was captured after the stop
# instant, capture overran whatever the document says.
stop_iso = res.get("stop_deadline") or res.get("args", {}).get("stop_deadline")
corpus = R / "run" / "raw_full.jsonl"
if stop_iso and corpus.exists():
    stop_ts = datetime.fromisoformat(stop_iso).timestamp()
    last = None
    with open(corpus, errors="replace") as fh:
        for line in fh:
            try:
                last = json.loads(line).get("recv_ts", last)
            except Exception:
                pass
    over = (last - stop_ts) if last else None
    ck("8. no record postdates the stop deadline",
       over is not None and over <= 2.0,
       f"last record {over:+.1f}s relative to the deadline"
       if over is not None else "no records")
else:
    ck("8. no record postdates the stop deadline", False,
       f"cannot evaluate (stop_deadline={stop_iso!r}, corpus={corpus.exists()})")

print()
print("VERIFY: " + ("PASSED" if not failures else f"FAILED -- {len(failures)} issue(s)"))
for f in failures:
    print(f"  - {f}")
sys.exit(1 if failures else 0)
