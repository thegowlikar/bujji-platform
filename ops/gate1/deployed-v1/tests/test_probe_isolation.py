"""The capacity probe must never move the Gate 1 verdict.

It deliberately subscribes beyond the measured universe, so if any of its
numbers could reach the grader the verdict would describe a subscription set
Gate 1 never measured.
"""
import importlib.util, sys, json, copy

sp = importlib.util.spec_from_file_location("g5", "/opt/bujji/gate1-run/gate1_measure_v5.py")
G5 = importlib.util.module_from_spec(sp); sys.modules["g5"] = G5; sp.loader.exec_module(G5)

CLEAN = {
    "validation": {"full": {"dropped": 0, "rejected": 0, "unaccounted": 0,
                            "raw_matches_written": True, "corpus_self_sufficient": True,
                            "harness_was_bottleneck": False,
                            "raw_lines_on_disk": 10, "corpus_written": 10}},
    "phases": [{"label": "opening_sustained", "mode": "full",
                "silent_symbols": {"counts": {"LOW_LIQUIDITY": 30}}},
               {"label": "opening_reconnect_after", "mode": "full",
                "full_restoration": True, "baseline_was_vacuous": False,
                "producers_before_reconnect": 180, "resumed_count": 180}],
    "feed_state": {"full": {"corpus": {"accounted": True}}},
    "artifact_seal": {"raw_full.jsonl": {"sha256": "x"}},
    "disk_trajectory": [{"t": 0, "free_bytes": 9e9}],
    "opening_coverage": {"status": "CONTAINED", "canonical_symbol_count": 249},
}

base = G5.grade_gate1(copy.deepcopy(CLEAN))
print(f"without any probe          -> {base['verdict']}")

PROBES = [
    ("probe hit a hard ceiling at 500",
     {"highest_accepted": 500, "ceiling_at": 750,
      "rungs": [{"requested": 750, "submit_ok": False, "symbols_producing": 0}]}),
    ("probe produced nothing at all",
     {"highest_accepted": 0, "rungs": [{"requested": 500, "submit_ok": True,
                                        "symbols_producing": 0}]}),
    ("probe errored outright", {"error": "RuntimeError: venue refused"}),
    ("probe reached the full 1465",
     {"highest_accepted": 1465, "rungs": [{"requested": 1465, "submit_ok": True,
                                           "symbols_producing": 1400}]}),
]

fails = 0
for name, probe in PROBES:
    doc = copy.deepcopy(CLEAN)
    doc["capacity_probe"] = probe
    v = G5.grade_gate1(doc)
    same = (v["verdict"] == base["verdict"]
            and len(v["failures"]) == len(base["failures"]))
    fails += (not same)
    print(f"  [{'OK ' if same else 'BAD'}] {name:34} -> {v['verdict']} "
          f"({len(v['failures'])} failures)")

print()
# POSITIVE CONTROL: the grader must still be capable of failing, or the
# isolation above is proved by a grader that never fails at all.
broken = copy.deepcopy(CLEAN)
broken["validation"]["full"]["dropped"] = 5
bv = G5.grade_gate1(broken)
print(f"CONTROL: a real defect still fails -> {bv['verdict']} "
      f"({len(bv['failures'])} failure(s))")
if bv["verdict"] != "FAIL":
    print("  CONTROL BROKEN: the grader cannot fail, so isolation proves nothing")
    fails += 1

print(f"\n{'PROBE IS VERDICT-NEUTRAL' if not fails else str(fails)+' PROBLEM(S)'}")
sys.exit(1 if fails else 0)
