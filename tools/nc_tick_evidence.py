"""Negative controls for the M2 read-side integration."""
import pathlib, subprocess, sys

ROOT = pathlib.Path("/opt/bujji/work-readiness")
EV = ROOT / "bujji/production_runtime/tick_evidence.py"
VERDICT = ROOT / "bujji/production_runtime/session_safety_verdict.py"
RUNNER = ROOT / "bujji_options_os_runner.py"

TESTS = ["tests/test_tick_evidence_is_consumed.py",
         "tests/test_pending_evidence_cannot_pass.py",
         "tests/test_session_safety_exit_code.py"]

CONTROLS = [
    ("NC1 certified collapses into safe", VERDICT,
     "        return self.safe and not self.pending_evidence",
     "        return self.safe",
     ["test_a_session_missing_its_evidence_is_not_certified",
      "test_certified_is_not_merely_an_alias_for_safe"]),

    ("NC2 the runner exits green on pending", RUNNER,
     "            return EXIT_PENDING_EVIDENCE",
     "            return EXIT_OK",
     ["test_a_session_that_never_inspected_prior_journals_is_not_certified",
      "test_an_unverified_tick_journal_is_not_certified"]),

    ("NC3 a failed verification is filed as merely unknown", EV,
     "    except (JournalNotFaithfulError, JournalIntegrityError) as exc:\n"
     "        # THE READER REACHED A VERDICT, AND IT IS BAD.",
     "    except (KeyboardInterrupt,) as exc:\n"
     "        # THE READER REACHED A VERDICT, AND IT IS BAD.",
     ["test_a_truncated_journal_fails_verification_though_it_self_reports_faithful",
      "test_an_altered_journal_fails_verification"]),

    ("NC4 inspection demands certification instead of observing", EV,
     "                result = open_journal(path, manifest if manifest.exists() else None,\n"
     "                                      purpose=PURPOSE_RESEARCH)",
     "                result = open_journal(path, manifest if manifest.exists() else None,\n"
     "                                      purpose=PURPOSE_SAFETY_CERTIFICATION)",
     ["test_a_prior_unsealed_journal_is_reported_incomplete_not_corrupt",
      "test_unparseable_bytes_in_an_UNSEALED_journal_are_incomplete_not_corrupt"]),

    ("NC5 an absent journal is treated as legitimately absent", VERDICT,
     '        pending.append(\n            "the session recorded nothing about a tick journal at all',
     '        return unsafe, pending\n        pending.append(\n            "the session recorded nothing about a tick journal at all',
     ["test_removing_any_evidence_source_removes_certification"]),

    ("NC6 a replay mismatch stops being unsafe", VERDICT,
     '    elif not replay.get("reproduced"):',
     '    elif False:',
     ["test_a_replay_mismatch_makes_the_session_unsafe",
      "test_a_replay_mismatch_is_UNSAFE"]),

    ("NC7 a corrupt prior journal stops being unsafe", VERDICT,
     '        for record in prior.get("corrupt") or ():',
     '        for record in ():',
     ["test_a_corrupt_prior_journal_makes_this_session_unsafe",
      "test_a_corrupt_prior_journal_is_UNSAFE"]),

    ("NC8 an unread journal certifies on the writer's word", VERDICT,
     '    verified = journal.get("verified")\n    if not isinstance(verified, dict):',
     '    verified = journal.get("verified")\n    if False:',
     ["test_an_unread_journal_is_pending_not_unsafe",
      "test_an_unverified_tick_journal_is_not_certified"]),

    ("NC9 the replay audit is allowed to reach an order path", EV,
     "        first = read_journal(path, manifest, purpose=PURPOSE_SAFETY_CERTIFICATION)",
     "        broker.place_order(None)\n        first = read_journal(path, manifest, purpose=PURPOSE_SAFETY_CERTIFICATION)",
     ["test_the_replay_audit_holds_no_broker_and_builds_no_order"]),
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
