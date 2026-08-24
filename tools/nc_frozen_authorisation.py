"""Negative controls for the SCOPED freeze authorisation.

The point of a narrow authorisation is that it stays narrow. Each control
makes an UNAUTHORISED change to a frozen file and confirms the build still
refuses it -- including a change to the very file that is authorised.
"""
import pathlib, subprocess, sys

ROOT = pathlib.Path("/opt/bujji/work-m4")
AUTHORISED = ROOT / "bujji/trading_brain/risk_governor/position_group_validation.py"
JOURNAL = ROOT / "bujji/journal/position_group_journal.py"
FOLD = ROOT / "bujji/trading_brain/risk_governor/position_group_fold.py"
MARGIN = ROOT / "bujji/trading_brain/risk_governor/whole_book_margin_provider.py"
GUARD = ROOT / "bujji/broker/guard.py"

TESTS = ["tests/test_frozen_vocabulary_extension.py", "tests/test_replay_engine_safety.py"]

CONTROLS = [
    ("NC1 an UNRELATED line added to the AUTHORISED file", AUTHORISED,
     "_REQUIRED_FIELDS = {",
     "UNRELATED_CONSTANT = 42\n\n_REQUIRED_FIELDS = {",
     ["test_every_added_line_belongs_to_the_authorised_extension"]),

    ("NC2 a line REMOVED from the authorised file", AUTHORISED,
     '        raise IllegalEventError("group already has a MINTED event; cannot mint twice")',
     '        pass',
     ["test_the_authorised_change_removes_nothing"]),

    ("NC3 a SECOND event type smuggled into the vocabulary", AUTHORISED,
     '    "SESSION_TRANSITION",\n})',
     '    "SESSION_TRANSITION",\n    "ARBITRARY_NEW_TYPE",\n})',
     ["test_the_extension_did_not_widen_the_vocabulary_beyond_one_type"]),

    ("NC4 the journal takes a convenience method", JOURNAL,
     "    def read_all_group_ids(self)",
     "    def convenience_helper(self):\n        return None\n\n    def read_all_group_ids(self)",
     ["test_named_frozen_files_are_individually_unchanged",
      "test_the_journal_itself_took_no_convenience_method",
      "test_the_rest_of_the_frozen_packages_are_still_byte_untouched",
      "test_execution_and_capital_packages_byte_untouched"]),

    ("NC5 the fold is altered", FOLD,
     "def net_quantity(leg: LegState) -> int:",
     "def unrelated_addition():\n    return None\n\n\ndef net_quantity(leg: LegState) -> int:",
     ["test_named_frozen_files_are_individually_unchanged",
      "test_the_rest_of_the_frozen_packages_are_still_byte_untouched",
      "test_execution_and_capital_packages_byte_untouched"]),

    ("NC6 the margin provider's active states are widened", MARGIN,
     "_ACTIVE_LIFECYCLE_STATES = (LIFECYCLE_CONSTRUCTED, LIFECYCLE_OPEN, LIFECYCLE_PARTIALLY_OPEN)",
     "_ACTIVE_LIFECYCLE_STATES = (LIFECYCLE_CONSTRUCTED, LIFECYCLE_OPEN, LIFECYCLE_PARTIALLY_OPEN, 'MINTED')",
     ["test_named_frozen_files_are_individually_unchanged",
      "test_the_rest_of_the_frozen_packages_are_still_byte_untouched",
      "test_execution_and_capital_packages_byte_untouched"]),

    ("NC7 a broker guard is altered", GUARD,
     "from",
     "# unauthorised\nfrom",
     ["test_named_frozen_files_are_individually_unchanged",
      "test_the_rest_of_the_frozen_packages_are_still_byte_untouched",
      "test_execution_and_capital_packages_byte_untouched"]),
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
