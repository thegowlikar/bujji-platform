"""Negative controls for the M3 migration.

Each removes ONE guard the migration added and names the tests that must go
red. A silent control means that test proves nothing about the guard.
"""
import pathlib, subprocess, sys

ROOT = pathlib.Path("/opt/bujji/work-readiness")
REG = ROOT / "bujji/production_runtime/position_reality_registry.py"
EXE = ROOT / "bujji/production_runtime/trade_lifecycle_executor.py"
LCR = ROOT / "bujji/production_runtime/position_lifecycle_runtime.py"
RUN = ROOT / "bujji_options_os_runner.py"
EOD = ROOT / "bujji/production_runtime/eod_closure.py"

TESTS = ["tests/test_position_truth_migration.py", "tests/test_exit_broker_truth.py"]

CONTROLS = [
    ("NC1 confirmed-flat becomes the negation of is_open", REG,
     "        return self.truth_state != STATE_UNKNOWN and not self.open_symbols",
     "        return not self.open_symbols",
     ["test_an_unreadable_account_is_neither_open_nor_confirmed_flat",
      "test_is_confirmed_flat_is_not_the_negation_of_is_open"]),

    ("NC2 an unreadable account resolves every group", REG,
     "        if truth.is_unknown:\n            return tuple(self._order)",
     "        if False:\n            return tuple(self._order)",
     ["test_an_unreadable_account_leaves_every_group_unresolved"]),

    ("NC3 positions_for_group reports no legs instead of raising", REG,
     '''        if truth.is_unknown:
            raise BrokerTruthUnknownError(''',
     '''        if truth.is_unknown:
            return []
        if False:
            raise BrokerTruthUnknownError(''',
     ["test_positions_for_group_raises_rather_than_reporting_no_legs"]),

    ("NC4 the executor closes a group on an unreadable account", EXE,
     "            if reality.is_confirmed_flat:",
     "            if not reality.is_open:",
     ["test_a_filled_exit_does_not_mark_closed_when_the_account_is_unreadable"]),

    ("NC5 the risk snapshot labels an unread account CLOSED", LCR,
     '"OPEN" if reality.may_still_be_open else "CLOSED",',
     '"OPEN" if reality.is_open else "CLOSED",',
     ["test_the_risk_snapshot_labels_an_unreadable_account_as_still_open"]),

    ("NC6 the runner turns a failed read into flatness", RUN,
     "        answer = _position_truth_for(self).read()\n"
     "        if answer.is_unknown:\n            return None, answer.detail",
     "        answer = _position_truth_for(self).read()\n"
     "        if False:\n            return None, answer.detail",
     ["test_broker_reports_flat_stays_three_valued",
      "test_broker_reports_flat_never_turns_a_failure_into_flatness"]),

    ("NC7 the EOD machine collapses a failed read into an empty book", EOD,
     "    if answer.is_unknown:\n        return None, answer.detail",
     "    if False:\n        return None, answer.detail",
     ["test_discover_broker_positions_stays_three_valued",
      "test_discover_broker_positions_distinguishes_empty_from_failed"]),

    ("NC8 the registry reads the broker directly again", REG,
     "        truth = await self._truth.read_async()\n        held = set(truth.symbols)",
     "        await self._broker.get_open_positions()\n"
     "        truth = await self._truth.read_async()\n        held = set(truth.symbols)",
     ["test_the_registry_has_no_direct_position_read_left",
      "test_each_registry_call_costs_exactly_one_broker_read"]),
]


def failing():
    out = subprocess.run(
        ["/opt/bujji/.venv/bin/python", "-m", "pytest", *TESTS, "-q", "--no-header",
         "-p", "no:cacheprovider", "--tb=no"],
        cwd=ROOT, capture_output=True, text=True).stdout
    return {ln.split("::")[-1].split("[")[0] for ln in out.splitlines() if ln.startswith("FAILED")}


base = failing()
if base:
    sys.exit(f"baseline not green: {sorted(base)}")
print("baseline: green\n")

bad = 0
for name, path, old, new, expected in CONTROLS:
    original = path.read_text()
    if old not in original:
        print(f"  INERT  {name}: anchor not found -- control never applied")
        bad = 1
        continue
    try:
        path.write_text(original.replace(old, new, 1))
        got = failing()
    finally:
        path.write_text(original)
    caught = sorted(e for e in expected if e in got)
    if caught:
        print(f"  caught {name}\n           -> {', '.join(caught)}")
    else:
        print(f"  SILENT {name}: expected {expected}, saw {sorted(got) or 'nothing'}")
        bad = 1

after = failing()
if after:
    sys.exit(f"NOT RESTORED: {sorted(after)}")
print("\nrestored: green")
sys.exit(bad)
