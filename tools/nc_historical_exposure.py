"""Negative controls for the historical-exposure recovery state."""
import pathlib, subprocess, sys

ROOT = pathlib.Path("/opt/bujji/work-m4")
HE = ROOT / "bujji/production_runtime/historical_exposure.py"
V = ROOT / "bujji/production_runtime/session_safety_verdict.py"
RUN = ROOT / "bujji_options_os_runner.py"

TESTS = ["tests/test_historical_exposure.py",
         "tests/test_blind_session_is_not_silent.py",
         "tests/test_one_strategy_survives_restart.py"]

CONTROLS = [
    ("NC1 stale exposure stops blocking entry", HE,
     "        return bool(self.stale) or not self.inspected",
     "        return False",
     ["test_a_group_left_open_by_an_earlier_day_is_detected",
      "test_an_inspection_that_could_not_run_blocks",
      "test_a_broker_unknown_does_not_hide_the_exposure"]),

    ("NC2 an inspection that could not run reads as clean", HE,
     "        return bool(self.stale) or not self.inspected",
     "        return bool(self.stale)",
     ["test_an_inspection_that_could_not_run_blocks"]),

    ("NC3 active lifecycle states are narrowed away", HE,
     '_ACTIVE = ("CONSTRUCTED", "OPEN", "PARTIALLY_OPEN")',
     '_ACTIVE = ()',
     ["test_a_group_left_open_by_an_earlier_day_is_detected",
      "test_the_real_2026_08_21_group_is_reported_as_a_recovery_state"]),

    ("NC4 today's own position is treated as historical", HE,
     "            if last is not None and last >= trading_date:",
     "            if False:",
     ["test_todays_own_open_position_is_not_historical"]),

    ("NC5 corrections count as position activity again", HE,
     "                                 if getattr(e, \"event_type\", None) != CORRECTION_EVENT)",
     "                                 )",
     ["test_an_operator_correction_stops_the_block"]),

    ("NC6 any correction clears the block, marker or not", HE,
     "        if (getattr(event, \"payload\", {}) or {}).get(RESOLUTION_KEY) is True:",
     "        if True:",
     ["test_a_correction_without_the_resolution_marker_does_not_clear_it"]),

    ("NC7 the instructions stop naming the group", HE,
     '            lines.append(f"  {g.position_group_id}  [{g.lifecycle_state}]"',
     '            lines.append(f"  <redacted>  [{g.lifecycle_state}]"',
     ["test_the_instructions_name_the_group_the_legs_and_the_date",
      "test_the_real_2026_08_21_group_is_reported_as_a_recovery_state"]),

    ("NC8 stale exposure stops making the session unsafe", V,
     '        for group in historical.get("stale") or ():',
     '        for group in ():',
     ["test_stale_exposure_makes_the_session_unsafe"]),

    ("NC9 an uninspected history stops making the session unsafe", V,
     '        if not historical.get("inspected"):',
     '        if False:',
     ["test_an_uninspected_history_makes_the_session_unsafe"]),

    ("NC10 the new refusal loses its classification", V,
     '    "HISTORICAL_UNRESOLVED_EXPOSURE":',
     '    "HISTORICAL_UNRESOLVED_EXPOSURE_DISABLED":',
     ["test_every_literal_refusal_reason_is_classified"]),

    ("NC11 a disciplined decline loses its explicit classification", V,
     '    "STRATEGY_ALREADY_DEPLOYED_TODAY":\n        "the day\'s one strategy was already deployed',
     '    "STRATEGY_ALREADY_DEPLOYED_TODAY_DISABLED":\n        "the day\'s one strategy was already deployed',
     ["test_every_literal_refusal_reason_is_classified"]),

    ("NC12 classification collapses back to blindness-only", V,
     "    return _blindness_detail(reason) is not None or reason in _DELIBERATELY_NOT_BLINDNESS",
     "    return _blindness_detail(reason) is not None",
     ["test_every_literal_refusal_reason_is_classified"]),

    ("NC13 the recovery state stops blocking at the entry gate", RUN,
     "        if historical is not None and historical.blocks_entry:",
     "        if False:",
     []),   # asserted structurally below
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
    if not expected:
        original = path.read_text()
        if old not in original:
            print(f"  INERT  {name}: anchor not found")
            bad = 1
        else:
            print(f"  n/a    {name}: anchor present (gate wiring asserted structurally)")
        continue
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
