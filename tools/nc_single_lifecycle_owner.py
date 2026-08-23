"""Negative controls for the single lifecycle owner."""
import pathlib, subprocess, sys

ROOT = pathlib.Path("/opt/bujji/work-m4")
LC = ROOT / "bujji/production_runtime/session_lifecycle.py"
GOV = ROOT / "bujji/production_runtime/trading_session_governor/session_governor.py"
TBR = ROOT / "bujji/production_runtime/trading_brain_runtime.py"

TESTS = ["tests/test_single_lifecycle_owner.py",
         "tests/test_lifecycle_failure_injection.py"]

CONTROLS = [
    ("NC1 a second component transitions POSITION_ACTIVE again", TBR,
     "        if all_filled:\n            # M4: RuntimeState NO LONGER TRANSITIONS",
     "        if all_filled:\n            machine.transition(RuntimeState.POSITION_ACTIVE, reason='x')\n            # M4: RuntimeState NO LONGER TRANSITIONS",
     ["test_only_one_runtime_component_transitions_to_position_active",
      "test_runtimestate_no_longer_transitions_position_active"]),

    ("NC2 the retired second entry gate is revived", TBR,
     "        if machine.state in (RuntimeState.INITIALIZING, RuntimeState.ERROR):",
     "        if machine.state not in _ENTRY_ACCEPTING_STATES__RETIRED_M4:",
     ["test_the_second_entry_gate_is_retired"]),

    ("NC3 a governor transition bypasses the journal", GOV,
     '            self._transition(TradingSessionState.MANAGING, "lifecycle_evaluation")',
     '            self._state_tracker.transition(TradingSessionState.MANAGING, reason="lifecycle_evaluation")',
     ["test_every_governor_transition_goes_through_the_journaled_path"]),

    ("NC4 the cache moves before the journal", GOV,
     "        journal = self._lifecycle_journal\n        if journal is not None:",
     "        self._state_tracker.transition(target, reason=reason)\n        journal = self._lifecycle_journal\n        if journal is not None:",
     ["test_every_governor_transition_goes_through_the_journaled_path"]),

    ("NC5 a broken transition chain is accepted", LC,
     "    broken = _chain_is_contiguous(transitions)\n    if broken:",
     "    broken = _chain_is_contiguous(transitions)\n    if False:",
     ["test_a_broken_transition_chain_is_unknown",
      "test_a_truncated_transition_chain_is_unknown"]),

    ("NC6 broker UNKNOWN stops blocking", LC,
     '    if broker_truth is None or getattr(broker_truth, "is_unknown", True):',
     '    if False:',
     ["test_broker_unknown_is_unknown", "test_broker_unknown_blocks_from_every_state",
      "test_no_broker_read_at_all_is_unknown"]),

    ("NC7 journal-says-open vs broker-flat stops being a mismatch", LC,
     "    if says_open and not broker_open:",
     "    if False:",
     ["test_journal_says_open_but_broker_is_flat_is_unknown",
      "test_partial_entry_is_unknown_when_the_broker_disagrees",
      "test_emergency_close_that_did_not_flatten_is_unknown"]),

    ("NC8 unexpected broker exposure stops being a mismatch", LC,
     "    if not says_open and broker_open:",
     "    if False:",
     ["test_broker_holds_what_the_journal_does_not_know_about_is_unknown",
      "test_crash_during_entry_with_the_broker_holding_it",
      "test_a_partial_exit_reported_as_exited_is_unknown"]),

    ("NC9 missing history reads as a fresh session", LC,
     '        return UNKNOWN, "no session transitions recorded"',
     '        return TradingSessionState.STRATEGY_LOCKED, "assumed fresh"',
     ["test_missing_history_is_unknown_and_blocks",
      "test_an_empty_journal_is_unknown_not_a_fresh_session"]),

    ("NC10 a corrupt position history is swallowed", LC,
     "    if journal_error:",
     "    if False:",
     ["test_a_corrupt_event_history_is_unknown",
      "test_a_corrupt_position_history_is_unknown"]),

    ("NC11 UNKNOWN starts permitting entry", LC,
     "        return self.state == TradingSessionState.STRATEGY_LOCKED",
     "        return self.state in (TradingSessionState.STRATEGY_LOCKED, UNKNOWN)",
     ["test_missing_history_is_unknown_and_blocks",
      "test_broker_unknown_blocks_from_every_state"]),

    ("NC12 an unrecognised recorded state is coerced", LC,
     '        return UNKNOWN, f"journal records an unrecognised state {raw!r}"',
     '        return TradingSessionState.ANALYSING_MARKET, "coerced"',
     ["test_an_unrecognised_recorded_state_is_unknown"]),
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
