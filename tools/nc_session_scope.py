"""Negative controls: every scope filter is load-bearing."""
import pathlib, subprocess, sys

ROOT = pathlib.Path("/opt/bujji/work-m4")
SCOPE = ROOT / "bujji/production_runtime/position_group_scope.py"
BRIDGE = ROOT / "bujji/production_runtime/execution_journal_bridge.py"
COMP = ROOT / "bujji/production_runtime/trading_brain_composition_root.py"
PRIOR = ROOT / "bujji/production_runtime/prior_fills.py"

TESTS = ["tests/test_session_scope_is_excluded.py"]

CONTROLS = [
    ("NC1 the sanctioned enumeration stops filtering", SCOPE,
     "    return [g for g in journal.read_all_group_ids() if not is_session_scoped(g)]",
     "    return list(journal.read_all_group_ids())",
     ["test_the_sanctioned_enumeration_excludes_them",
      "test_a_session_event_can_be_written_with_no_position_group"]),

    ("NC2 recovery/closure enumeration stops filtering", BRIDGE,
     "    return [g for g in rows if not is_session_scoped(g)]",
     "    return rows",
     ["test_recovery_and_closure_enumeration_excludes_session_rows"]),

    ("NC3 the frozen package gets the RAW journal", COMP,
     "        journal=scoped_journal, margin_provider=margin_provider",
     "        journal=journal, margin_provider=margin_provider",
     ["test_the_composition_root_scopes_the_journal_before_the_frozen_package"]),

    ("NC4 the scoped view stops hiding session ids", SCOPE,
     "    def read_all_group_ids(self) -> List[str]:\n        return position_group_ids(self._journal)",
     "    def read_all_group_ids(self) -> List[str]:\n        return self._journal.read_all_group_ids()",
     ["test_the_scoped_journal_hides_session_ids",
      "test_a_session_row_is_never_projected_for_margin"]),

    ("NC5 a session identity is silently allowed through the position view", SCOPE,
     "        if is_session_scoped(position_group_id):",
     "        if False:",
     ["test_the_scoped_journal_refuses_a_session_identity_outright"]),

    ("NC6 session events are no longer stripped from a group read", SCOPE,
     "    return [e for e in events if getattr(e, \"event_type\", None) != SESSION_EVENT_TYPE]",
     "    return list(events)",
     ["test_session_events_are_stripped_from_any_event_list"]),

    ("NC7 the restart guard enumerates unfiltered", PRIOR,
     "    for group_id in position_group_ids(journal):",
     "    for group_id in journal.read_all_group_ids():",
     ["test_the_restart_guard_ignores_session_rows",
      "test_no_runtime_module_calls_read_all_group_ids_directly"]),

    ("NC8 the scope predicate stops recognising session identities", SCOPE,
     "    return isinstance(group_id, str) and group_id.startswith(SESSION_SCOPE_PREFIX)",
     "    return False",
     ["test_a_session_identity_is_recognised_as_session_scoped",
      "test_the_sanctioned_enumeration_excludes_them"]),

    ("NC9 the same-state transition guard is removed", ROOT / "bujji/trading_brain/risk_governor/position_group_validation.py",
     '        if payload["prior_state"] == payload["next_state"]:',
     '        if False:',
     ["test_a_transition_to_the_same_state_is_refused"]),
    ("NC10 a session event on a real group id is accepted", SCOPE,
     "    if event_type == SESSION_EVENT_TYPE and not session_id_shaped:",
     "    if False:",
     ["test_a_session_event_on_a_real_group_identity_is_refused",
      "test_a_refused_write_never_reaches_the_journal",
      "test_inconsistent_scopes_fail_the_assertion"]),

    ("NC11 a position event on a session id is accepted", SCOPE,
     "    if event_type != SESSION_EVENT_TYPE and session_id_shaped:",
     "    if False:",
     ["test_a_position_event_on_a_session_identity_is_refused",
      "test_inconsistent_scopes_fail_the_assertion"]),

    ("NC12 the refusal is raised but never recorded", SCOPE,
     "        record_scope_violation(position_group_id, event_type, str(exc), logger)",
     "        pass",
     ["test_a_refused_write_is_recorded_as_a_fact"]),

    ("NC13 the guard refuses everything, including valid writes", SCOPE,
     "def assert_scope_consistent(position_group_id, event_type) -> None:",
     "def assert_scope_consistent(position_group_id, event_type) -> None:\n"
     "    raise SessionScopeViolation('always')",
     ["test_both_well_formed_combinations_are_accepted",
      "test_consistent_scopes_pass_the_assertion"]),
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
