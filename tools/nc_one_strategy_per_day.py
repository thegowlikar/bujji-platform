"""Negative controls for the second-entry-after-flat-exit fix."""
import pathlib, subprocess, sys

ROOT = pathlib.Path("/opt/bujji/work-readiness")
PRIOR = ROOT / "bujji/production_runtime/prior_fills.py"
RUNNER = ROOT / "bujji_options_os_runner.py"
VERDICT = ROOT / "bujji/production_runtime/session_safety_verdict.py"
TESTS = ["tests/test_one_strategy_survives_restart.py",
         "tests/test_replay_engine_safety.py"]

CONTROLS = [
    ("NC1 the gate stops refusing", RUNNER,
     "        prior_fills = getattr(self, \"_prior_fills_today\", None)\n        if prior_fills:",
     "        prior_fills = getattr(self, \"_prior_fills_today\", None)\n        if False:",
     ["test_a_restart_after_a_completed_trade_refuses_the_second_entry",
      "test_an_unreadable_journal_refuses_and_says_it_could_not_look"]),

    ("NC2 dates are compared as text instead of in IST", PRIOR,
     "            if when.astimezone(IST).date().isoformat() == trading_date:",
     "            if str(when)[:10] == trading_date:",
     ["test_the_date_is_parsed_not_prefix_matched"]),

    ("NC3 a fill with no timestamp is skipped instead of counted", PRIOR,
     "                found.append(group_id)\n                break",
     "                break",
     ["test_an_unparseable_timestamp_counts_rather_than_vanishes"]),

    ("NC4 any event burns the day instead of a fill", PRIOR,
     "            if event.event_type != FILL_EVENT:\n                continue",
     "            if False:\n                continue",
     ["test_a_group_that_never_filled_does_not_burn_the_day"]),

    ("NC5 the snapshot is re-queried inside the gate", RUNNER,
     "        prior_fills = getattr(self, \"_prior_fills_today\", None)",
     "        from bujji.production_runtime.prior_fills import group_ids_filled_on\n"
     "        prior_fills = group_ids_filled_on(self._journal, self._as_of_date)",
     ["test_the_prior_fills_snapshot_is_read_once_at_startup_not_in_the_gate"]),

    ("NC6 an unreadable journal reads as nothing traded", PRIOR,
     "        return [UNREADABLE_JOURNAL], True",
     "        return [], False",
     ["test_an_unreadable_journal_is_not_an_empty_one",
      "test_the_unreadable_marker_reaches_the_gate_as_a_refusal"]),

    ("NC9 the snapshot swallows a real result", PRIOR,
     "        return group_ids_filled_on(journal, trading_date), False",
     "        return [], False",
     ["test_a_readable_journal_reports_what_it_found"]),

    ("NC7 a disciplined decline is escalated as blindness", VERDICT,
     '    "PRIOR_FILLS_UNREADABLE":',
     '    "STRATEGY_ALREADY_DEPLOYED_TODAY":\n        "escalated",\n    "PRIOR_FILLS_UNREADABLE":',
     ["test_a_disciplined_decline_does_not_page_the_operator"]),

    ("NC8 the unreadable case stops being blindness", VERDICT,
     '    "PRIOR_FILLS_UNREADABLE":\n        "the position group journal could not be read, so whether this "',
     '    "PRIOR_FILLS_UNREADABLE_DISABLED":\n        "the position group journal could not be read, so whether this "',
     ["test_but_failing_to_look_does"]),
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
