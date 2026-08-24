"""Negative controls for the broker-truth boundary.

Each entry removes exactly ONE guard and names the tests that must go red.
A control that leaves the suite green means that test proves nothing.
"""
import pathlib, subprocess, sys

ROOT = pathlib.Path("/opt/bujji/work-readiness")
READER = ROOT / "bujji/broker_truth/reader.py"
MODELS = ROOT / "bujji/broker_truth/models.py"

CONTROLS = [
    ("NC1 failed read collapses to flat", READER,
     'return self._unknown(\n                f"position read failed: {type(exc).__name__}: {exc}")',
     'return flat(f"read failed {exc}", self._source, self._schema_verified)',
     ["test_a_read_that_raises_is_unknown_not_flat"]),

    ("NC2 unreadable row is skipped instead of poisoning the read", READER,
     '''return self._unknown(
                    f"position row {index} ({symbol}) has an unreadable "
                    f"quantity {raw_qty!r} -- a partially understood position "
                    f"book is more dangerous than an unreadable one, because "
                    f"it looks complete")''',
     'continue',
     ["test_one_unreadable_row_poisons_the_whole_read",
      "test_payloads_this_adapter_cannot_understand_are_unknown"]),

    ("NC3 zero-quantity rows count as holdings", READER,
     'if quantity <= 0:\n                continue        # a closed or zero row is not a holding',
     'if False:\n                continue',
     ["test_a_zero_quantity_row_is_not_a_holding"]),

    ("NC4 UNKNOWN inherits the adapter's schema flag", READER,
     'return unknown(detail, self._source)',
     'return unknown(detail, self._source, self._schema_verified)',
     ["test_an_unknown_answer_never_claims_schema_verification"]),

    ("NC5 a non-mapping row is inferred rather than refused", READER,
     'if not hasattr(row, "get"):',
     'if False:',
     ["test_payloads_this_adapter_cannot_understand_are_unknown"]),

    ("NC6 absent leg quantity reports zero", MODELS,
     '        return None\n\n    def require_known',
     '        return 0\n\n    def require_known',
     ["test_quantity_for_an_absent_leg_is_none_not_zero"]),

    ("NC7 contradictory states are constructable", MODELS,
     'if self.state == STATE_CONFIRMED_OPEN and not self.legs:',
     'if False:',
     ["test_confirmed_open_with_no_legs_is_refused"]),

    ("NC8 non-open states may carry legs", MODELS,
     'if self.state != STATE_CONFIRMED_OPEN and self.legs:',
     'if False:',
     ["test_a_non_open_state_carrying_legs_is_refused"]),
]

TEST = "tests/test_broker_truth_boundary.py"


def failing_names():
    out = subprocess.run(
        ["/opt/bujji/.venv/bin/python", "-m", "pytest", TEST, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True).stdout
    return {ln.split("::")[-1].split("[")[0] for ln in out.splitlines() if ln.startswith("FAILED")}


baseline = failing_names()
if baseline:
    sys.exit(f"baseline is not green: {baseline}")
print("baseline: green\n")

verdict = 0
for name, path, old, new, expected in CONTROLS:
    original = path.read_text()
    if old not in original:
        print(f"  INERT  {name}: anchor text not found -- control never applied")
        verdict = 1
        continue
    try:
        path.write_text(original.replace(old, new, 1))
        got = failing_names()
    finally:
        path.write_text(original)
    caught = {e for e in expected if e in got}
    if caught:
        print(f"  caught {name}\n           -> {', '.join(sorted(caught))}")
    else:
        print(f"  SILENT {name}: expected {expected}, saw {sorted(got) or 'nothing'}")
        verdict = 1

after = failing_names()
if after:
    sys.exit(f"tree not restored: {after}")
print("\nrestored: green")
sys.exit(verdict)
