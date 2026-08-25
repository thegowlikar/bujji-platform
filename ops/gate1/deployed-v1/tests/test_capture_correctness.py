"""Capture-correctness proof. No market, no network.

Every case has a known answer, and every guard has a control showing the check
is what produces the result.
"""
import importlib.util, json, hashlib, sys, tempfile, threading, time, subprocess
from pathlib import Path

RUN = Path("/opt/bujji/gate1-run")
sp = importlib.util.spec_from_file_location("g5", str(RUN / "gate1_measure_v5.py"))
G5 = importlib.util.module_from_spec(sp); sys.modules["g5"] = G5; sp.loader.exec_module(G5)

ok = fail = 0
def check(name, cond, detail=""):
    global ok, fail
    if cond: ok += 1
    else: fail += 1
    print(f"  [{'OK ' if cond else 'BAD'}] {name}" + (f" -- {detail}" if detail else ""))


print("1. SDK PAYLOAD MUTATION AFTER CALLBACK RETURN")
d = Path(tempfile.mkdtemp())
c = G5.RawCorpus(d / "raw.jsonl")
payload = {"symbol": "NSE:X", "ltp": 100.0, "bid_price": 99.5}
c.offer(seq=1, recv_wall=1.0, recv_mono=1.0, tid=7, payload=payload)
# The SDK reuses its dict the instant the callback returns.
payload["ltp"] = 999999.0
payload["symbol"] = "NSE:MUTATED"
payload["bid_price"] = None
time.sleep(0.6); c.close()
rec = json.loads((d / "raw.jsonl").read_text().strip())
check("stored payload is the ARRIVED value", rec["payload"]["ltp"] == 100.0,
      f"stored ltp={rec['payload']['ltp']}")
check("mutation did not reach the corpus", rec["payload"]["symbol"] == "NSE:X")
check("nulling a field after return did not reach it",
      rec["payload"]["bid_price"] == 99.5)
# CONTROL: the mutation really did happen, so the test is not vacuous.
check("CONTROL: the dict really was mutated", payload["ltp"] == 999999.0)

print("\n2. SNAPSHOT SERIALIZATION FAILURE")
d = Path(tempfile.mkdtemp())
c = G5.RawCorpus(d / "raw.jsonl")
class Hostile:
    def __repr__(self): raise RuntimeError("cannot repr")
    def __str__(self): raise RuntimeError("cannot str")
c.offer(seq=1, recv_wall=1.0, recv_mono=1.0, tid=1, payload={"bad": Hostile()})
c.offer(seq=2, recv_wall=2.0, recv_mono=2.0, tid=1, payload={"symbol": "NSE:Y", "ltp": 5})
time.sleep(0.6); c.close()
a = c.accounting()
check("snapshot failure counted as rejected", a["rejected"] == 1, str(a["rejected"]))
check("failure category preserved", bool(a["snapshot_failures"]),
      a["snapshot_failures"][0]["category"] if a["snapshot_failures"] else "none")
check("accounting identity holds", a["accounted"],
      f"offered={a['offered']} w={a['written']} r={a['rejected']} d={a['dropped']}")
check("good record still stored", a["written"] == 1)

print("\n3. QUEUE OVERFLOW")
d = Path(tempfile.mkdtemp())
saved = G5.QUEUE_MAX; G5.QUEUE_MAX = 5
c = G5.RawCorpus(d / "raw.jsonl")
c._stop.set(); time.sleep(0.4)          # stall the writer so the queue fills
for i in range(50):
    c.offer(seq=i + 1, recv_wall=float(i), recv_mono=float(i), tid=1,
            payload={"symbol": "NSE:Z", "ltp": i})
a = c.accounting()
check("overflow counted as dropped", a["dropped"] > 0, f"dropped={a['dropped']}")
# Mid-run the identity must account for records STILL IN FLIGHT. Omitting them
# made a benign in-flight state look identical to real silent loss.
check("identity holds mid-run including in-flight", a["accounted"],
      f"offered={a['offered']} written={a['written']} rejected={a['rejected']} "
      f"dropped={a['dropped']} in_flight={a['in_flight']}")
check("mid-run accounting is NOT yet settled", a["settled"] is False,
      "settled only after drain")
check("no unaccounted records", a["unaccounted"] == 0, str(a["unaccounted"]))
G5.QUEUE_MAX = saved

print("\n4. WALL-CLOCK JUMP -- monotonic gaps must be unaffected")
m = G5.Metrics({"NSE:A": "OPTION"})
base_w, base_m = 1_700_000_000.0, 500.0
for i in range(5):
    m.observe({"symbol": "NSE:A", "ltp": 1.0}, base_w + i, base_w + i, 50, 1,
              recv_mono=base_m + i, proc_mono=base_m + i)
# NTP steps the wall clock back an hour; monotonic keeps advancing by 1s.
for i in range(5, 10):
    m.observe({"symbol": "NSE:A", "ltp": 1.0}, base_w + i - 3600, base_w + i - 3600,
              50, 1, recv_mono=base_m + i, proc_mono=base_m + i)
s = m.summary(["NSE:A"], {})
g = s["interarrival_s"]
check("every gap is 1s despite the wall-clock step",
      g and abs(g["max"] - 1.0) < 1e-6 and abs(g["min"] - 1.0) < 1e-6,
      f"min={g['min']} max={g['max']}")
check("no false non-monotonic recv from the step",
      s["recv_time_nonmonotonic"] == 0, str(s["recv_time_nonmonotonic"]))
check("gap clock is declared as monotonic", s["gap_clock"] == "monotonic")
# CONTROL: with wall clock the same step WOULD corrupt the distribution.
m2 = G5.Metrics({"NSE:A": "OPTION"})
for i in range(5):
    m2.observe({"symbol": "NSE:A", "ltp": 1.0}, base_w + i, base_w + i, 50, 1)
for i in range(5, 10):
    m2.observe({"symbol": "NSE:A", "ltp": 1.0}, base_w + i - 3600, base_w + i - 3600, 50, 1)
g2 = m2.summary(["NSE:A"], {})["interarrival_s"]
check("CONTROL: wall clock alone WOULD be corrupted by the step",
      g2["min"] < -1000, f"wall-clock min gap={g2['min']}")

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
