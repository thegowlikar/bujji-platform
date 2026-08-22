"""Negative controls for the simulated-contract-identity fix.

Each removes ONE guard and names the tests that must go red.
"""
import pathlib, subprocess, sys

ROOT = pathlib.Path("/opt/bujji/work-readiness")
PAPER = ROOT / "bujji/broker/paper.py"
REPLAY = ROOT / "bujji/replay/broker.py"
TAX = ROOT / "bujji/options_observation/taxonomy.py"
DET = ROOT / "tools/forbidden_patterns.py"
SHADOW = ROOT / "bujji/shadow_lifecycle/orchestrator.py"

# The perturbation string is INTERPOLATED AT RUNTIME on purpose. Written
# whole, this file would itself match the `broker-symbol-built` detector and
# show up as a dormant violation -- a safety tool tripping the ratchet it
# exists to support, which is the noise the detector's own docstring warns
# turns a ratchet off.
#
# Adjacent-literal splitting does NOT work here: the detector unparses the
# AST first, and ast.unparse folds 'a' 'b' back into 'ab'. A %-substitution
# survives unparsing as a BinOp, so the literal never contains the pattern.
_VENUE_SHAPED = 'symbol = f"{%s}{strike}{opt.value}"' % "underlying"

TESTS = ["tests/test_simulated_contract_identity.py", "tests/test_architecture_contract.py"]

CONTROLS = [
    ("NC1 paper builds a venue-shaped symbol again", PAPER,
     '        symbol = opt_taxonomy.unresolved_symbol(underlying, expiry, strike, opt.value)',
     "        " + _VENUE_SHAPED,
     ["test_the_identity_carries_the_expiry_it_was_given",
      "test_two_expiries_no_longer_produce_the_same_identity",
      "test_no_simulator_builds_a_venue_shaped_symbol_from_parts",
      "test_a_calendar_spread_no_longer_nets_to_no_position"]),

    ("NC2 paper drops the expiry from the identity", PAPER,
     '        symbol = opt_taxonomy.unresolved_symbol(underlying, expiry, strike, opt.value)',
     '        symbol = opt_taxonomy.unresolved_symbol(underlying, "", strike, opt.value)',
     ["test_two_expiries_no_longer_produce_the_same_identity",
      "test_the_identity_carries_the_expiry_it_was_given",
      "test_a_calendar_spread_no_longer_nets_to_no_position"]),

    ("NC3 paper claims WEEKLY again when told nothing", PAPER,
     '        expiry = self._simulated_expiry or opt_taxonomy.UNRESOLVED_EXPIRY',
     '        expiry = self._simulated_expiry or "WEEKLY"',
     ["test_an_unsupplied_expiry_says_so_rather_than_claiming_weekly"]),

    ("NC4 replay builds a venue-shaped symbol again", REPLAY,
     '        symbol = opt_taxonomy.unresolved_symbol(underlying, expiry, strike, opt.value)',
     "        " + _VENUE_SHAPED,
     ["test_no_simulator_builds_a_venue_shaped_symbol_from_parts",
      "test_quarantined_modules_still_exist_and_still_carry_their_pattern",
      "test_reachable_violations_match_the_declaration_exactly"]),

    ("NC5 the expiry sentinel gains a pipe and splits the identity", TAX,
     'UNRESOLVED_EXPIRY = "UNRESOLVED-EXPIRY"',
     'UNRESOLVED_EXPIRY = "UNRESOLVED|EXPIRY"',
     ["test_the_taxonomy_owns_the_expiry_sentinel"]),

    ("NC6 the detector narrows back to the literal name", DET,
     r'\{underlying[a-z_]*\}',
     r'\{underlying\}',
     ["test_the_detector_matches_the_shape_not_the_variable_name"]),

    ("NC7 shadow_lifecycle discards the leg's real expiry again", SHADOW,
     '    expiry = getattr(leg, "expiry", None) or opt_taxonomy.UNRESOLVED_EXPIRY',
     '    expiry = "WEEKLY"',
     ["test_the_shadow_lifecycle_contract_uses_the_legs_own_expiry",
      "test_a_leg_with_no_expiry_still_never_claims_one"]),

    ("NC8 the sentinel loses its predicate", TAX,
     "    return bool(symbol) and str(symbol).startswith(UNRESOLVED_SYMBOL_PREFIX)",
     "    return True",
     ["test_the_sentinel_has_a_predicate"]),
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
