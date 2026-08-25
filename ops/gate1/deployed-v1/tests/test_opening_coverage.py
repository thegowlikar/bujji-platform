"""Opening-coverage semantics, tested without a market.

Each case builds a results document of the shape the harness produces and
grades it. The grading logic is the real one, imported, not a copy.
"""
import importlib.util, json, sys, copy

sp = importlib.util.spec_from_file_location("g5", "/opt/bujji/gate1-run/gate1_measure_v5.py")
G5 = importlib.util.module_from_spec(sp); sys.modules["g5"] = G5; sp.loader.exec_module(G5)

BASE = {
    "validation": {"full": {"dropped": 0, "rejected": 0, "unaccounted": 0,
                            "raw_matches_written": True, "corpus_self_sufficient": True,
                            "harness_was_bottleneck": False,
                            "raw_lines_on_disk": 10, "corpus_written": 10}},
    "phases": [{"label": "opening_sustained", "mode": "full",
                "silent_symbols": {"counts": {"LOW_LIQUIDITY": 30}}}],
    "feed_state": {"full": {"corpus": {"accounted": True}}},
    "artifact_seal": {"raw_full.jsonl": {"sha256": "x"}},
    "disk_trajectory": [{"t": 0, "free_bytes": 9e9}],
}


def grade(coverage, apply_containment=True):
    """Mirrors the harness's final grading step."""
    r = copy.deepcopy(BASE)
    r["opening_coverage"] = coverage
    v = G5.grade_gate1(r)
    if apply_containment:
        cov = r.get("opening_coverage") or {}
        if cov.get("status") == "GAP":
            v["failures"].append(
                f"OPENING COVERAGE GAP: {cov['missing_from_provisional_count']} "
                f"canonical symbol(s) were not subscribed before the bell")
        elif cov.get("status") != "CONTAINED":
            v["failures"].append(
                f"OPENING COVERAGE UNPROVEN: {cov.get('status')}")
    v["verdict"] = "FAIL" if v["failures"] else "PASS"
    return v


CASES = [
    ("canonical inside provisional",
     {"status": "CONTAINED", "canonical_symbol_count": 249,
      "provisional_symbol_count": 369, "contextual_only_count": 120}, "PASS"),
    ("canonical OUTSIDE provisional",
     {"status": "GAP", "missing_from_provisional_count": 12,
      "missing_from_provisional": ["NSE:X"]}, "FAIL"),
    ("first spot never arrived",
     {"status": "CANONICAL_UNIVERSE_MISSING",
      "reason": "no canonical universe handed over"}, "FAIL"),
    ("dynamic symbol added late",
     {"status": "GAP", "missing_from_provisional_count": 1,
      "missing_from_provisional": ["NSE:LATE"],
      "late_subscription_submitted_ist": "2026-08-24T09:22:11+05:30"}, "FAIL"),
]

print("OPENING-COVERAGE SEMANTICS\n")
fails = 0
for name, cov, expect in CASES:
    v = grade(cov)
    ok = v["verdict"] == expect
    fails += (not ok)
    print(f"  [{'OK ' if ok else 'BAD'}] {name:34} -> {v['verdict']:4} (want {expect})")
    if v["failures"]:
        print(f"          {v['failures'][-1][:88]}")

print("\nCONTEXTUAL COVERAGE IS NOT REQUIRED COVERAGE")
v = grade({"status": "CONTAINED", "canonical_symbol_count": 249,
           "provisional_symbol_count": 369, "contextual_only_count": 120})
print(f"  120 provisional-only symbols present -> {v['verdict']} "
      f"(they are context, never a requirement)")
fails += (v["verdict"] != "PASS")

print("\nNEGATIVE CONTROL -- remove the containment check")
gap = {"status": "GAP", "missing_from_provisional_count": 12,
       "missing_from_provisional": ["NSE:X"]}
with_check = grade(gap, apply_containment=True)
without_check = grade(gap, apply_containment=False)
print(f"  with containment check   : {with_check['verdict']}")
print(f"  WITHOUT containment check: {without_check['verdict']}")
if without_check["verdict"] == "PASS" and with_check["verdict"] == "FAIL":
    print("  CONTROL HOLDS: removing the check lets a real opening gap pass as")
    print("  a clean run -- so the check is what catches it, not something else.")
else:
    print("  CONTROL BROKEN: the check is not what produces the FAIL")
    fails += 1

print(f"\n{'ALL OK' if not fails else str(fails) + ' PROBLEM(S)'}")
sys.exit(1 if fails else 0)
