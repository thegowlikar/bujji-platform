"""M2's read side has a production consumer, and it terminates something.

M2 built a durable tick journal with a manifest and a replay module reporting
FAITHFUL / INCOMPLETE / CORRUPT, gated by purpose. The WRITE side was wired.
The READ side -- `read_journal`, `recover_unsealed`, `open_journal`,
`payloads`, `ReplayResult.require` -- had NO production caller at all: built,
tested, never reached. That is the defect class this campaign exists to
remove, and M2 introduced an instance of it.

The consequence was concrete. `tick_journal.faithful` in the session summary
was the WRITER stating its own drop count; nothing opened the file back up. A
journal truncated after its last fsync, or altered on disk, self-reports
faithful and the session exits 0.
"""
from __future__ import annotations

import json
import logging
import pathlib

import pytest

from bujji.production_runtime.tick_evidence import (
    NOT_FAITHFUL, UNVERIFIABLE, VERIFIED, inspect_prior_journals,
    reproduce_recorded_ticks, verify_sealed_journal)
from bujji.production_runtime.session_safety_verdict import evaluate_session_safety
from bujji.tick_journal import (
    MANIFEST_VERSION, STATE_CORRUPT, STATE_FAITHFUL, STATE_INCOMPLETE,
    JournalManifest, TickJournal)

LOG = logging.getLogger("test")


def _sealed(tmp_path, session_id="S1", payloads=3, name=None):
    """A real journal, written and sealed exactly as the runner does."""
    path = tmp_path / (name or f"{session_id}.jsonl")
    journal = TickJournal(path, session_id=session_id, logger=LOG)
    for i in range(payloads):
        journal.offer({"symbol": f"NSE:X{i}", "ltp": 100.0 + i})
    stats = journal.close()
    JournalManifest(
        version=MANIFEST_VERSION, session_id=session_id, as_of_date="2026-08-24",
        journal_filename=path.name, started_at="2026-08-24T09:15:00+05:30",
        ended_at="2026-08-24T15:30:00+05:30",
        offered=stats.offered, written=stats.written, dropped=stats.dropped,
        max_queue_depth=stats.max_queue_depth, bytes_written=stats.bytes_written,
        content_sha256=journal.content_sha256(),
        fsync_every_records=journal._fsync_every_records,
        fsync_every_seconds=journal._fsync_every_seconds,
        max_queue=journal._queue.maxsize, universe_symbols=1,
    ).write(path.with_suffix(".manifest.json"))
    return path


def _unsealed(tmp_path, session_id="OLD", payloads=2):
    """A journal an earlier process died before sealing."""
    path = tmp_path / f"{session_id}.jsonl"
    journal = TickJournal(path, session_id=session_id, logger=LOG)
    for i in range(payloads):
        journal.offer({"symbol": f"NSE:Y{i}", "ltp": 50.0 + i})
    journal.close()                       # flushes, but writes NO manifest
    return path


# --------------------------------------------------------------------------
# SHUTDOWN: the writer's claim is replaced by a reader's verdict.
# --------------------------------------------------------------------------

def test_a_sealed_journal_verifies(tmp_path):
    evidence = verify_sealed_journal(_sealed(tmp_path), LOG)
    assert evidence.outcome == VERIFIED
    assert evidence.state == STATE_FAITHFUL
    assert evidence.verified_faithful
    assert evidence.records == 3


def test_a_truncated_journal_fails_verification_though_it_self_reports_faithful(tmp_path):
    """THE case. The manifest says what should be there; the file no longer
    matches. Nothing before this ever compared them."""
    path = _sealed(tmp_path, payloads=5)
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:2]) + "\n")      # a kill -9 after fsync

    evidence = verify_sealed_journal(path, LOG)
    assert evidence.outcome == NOT_FAITHFUL, (
        "the writer's counters said faithful; the file on disk does not agree")
    assert not evidence.verified_faithful


def test_an_altered_journal_fails_verification(tmp_path):
    path = _sealed(tmp_path)
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    rows[0]["payload"] = {"symbol": "NSE:TAMPERED", "ltp": 1.0}
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    assert verify_sealed_journal(path, LOG).outcome == NOT_FAITHFUL


def test_an_unsealed_journal_cannot_be_verified(tmp_path):
    evidence = verify_sealed_journal(_unsealed(tmp_path), LOG)
    assert evidence.outcome == UNVERIFIABLE
    assert "never sealed" in evidence.detail


def test_verification_never_raises_in_teardown(tmp_path):
    """It runs in `_shutdown`, where a failure must not replace the session's
    real result with its own."""
    assert verify_sealed_journal(tmp_path / "does-not-exist.jsonl", LOG).outcome \
        == UNVERIFIABLE


# --------------------------------------------------------------------------
# STARTUP: what earlier processes left behind.
# --------------------------------------------------------------------------

def test_a_prior_unsealed_journal_is_reported_incomplete_not_corrupt(tmp_path):
    """An ordinary crash remnant. Using a CERTIFYING purpose here would make
    `require` raise and file every one of these as CORRUPT -- wrong severity,
    and it would mark healthy sessions unsafe."""
    _unsealed(tmp_path)
    evidence = inspect_prior_journals(tmp_path, "S-CURRENT", LOG)

    assert evidence.inspected
    assert len(evidence.unsealed) == 1
    assert evidence.unsealed[0]["state"] == STATE_INCOMPLETE
    assert evidence.corrupt == []
    assert not evidence.any_corrupt


def test_a_prior_SEALED_journal_that_does_not_match_its_manifest_is_corrupt(tmp_path):
    """CORRUPT is reserved for a journal that CLAIMED to be complete and is
    not. `read_journal` raises JournalIntegrityError on the hash mismatch, and
    inspection files that as a finding."""
    path = _sealed(tmp_path, session_id="OLD-SEALED", payloads=5)
    path.write_text(path.read_text().splitlines()[0] + "\n")

    evidence = inspect_prior_journals(tmp_path, "S-CURRENT", LOG)
    assert evidence.any_corrupt
    assert "IntegrityError" in evidence.corrupt[0]["error"]


def test_unparseable_bytes_in_an_UNSEALED_journal_are_incomplete_not_corrupt(tmp_path):
    """An unsealed file never claimed to be complete, so nothing readable
    surviving in it is INCOMPLETE -- the honest state -- not CORRUPT. Getting
    this backwards would mark a healthy session unsafe after any ordinary
    crash, which is how a useful alarm becomes one people switch off."""
    path = _unsealed(tmp_path)
    path.write_bytes(b"\x00\x01 not json at all\n")

    evidence = inspect_prior_journals(tmp_path, "S-CURRENT", LOG)
    assert not evidence.any_corrupt
    assert evidence.unsealed[0]["state"] == STATE_INCOMPLETE


def test_this_sessions_own_journal_is_not_reported_as_a_prior_one(tmp_path):
    _unsealed(tmp_path, session_id="S-CURRENT")
    evidence = inspect_prior_journals(tmp_path, "S-CURRENT", LOG)
    assert evidence.unsealed == [] and evidence.corrupt == []


def test_a_sealed_prior_journal_is_not_flagged(tmp_path):
    _sealed(tmp_path, session_id="OLD-SEALED")
    evidence = inspect_prior_journals(tmp_path, "S-CURRENT", LOG)
    assert evidence.unsealed == [] and evidence.corrupt == []


def test_inspection_records_that_it_could_not_run(tmp_path):
    """`inspected` stays False so nothing downstream mistakes silence for a
    clean result."""
    evidence = inspect_prior_journals(object(), "S", LOG)
    assert evidence.inspected is False
    assert evidence.error


# --------------------------------------------------------------------------
# POST-SESSION: the replay audit, which cannot order.
# --------------------------------------------------------------------------

def test_recorded_inputs_reproduce(tmp_path):
    reproduced, report = reproduce_recorded_ticks(_sealed(tmp_path), LOG)
    assert reproduced
    assert report["records"] == 3
    assert report["scope"] == "INPUT_REPRODUCTION_ONLY", (
        "this proves inputs reproduce, NOT that they reproduce a decision "
        "trail -- no decision trail with inputs is recorded anywhere yet")


def test_a_corrupted_journal_does_not_reproduce(tmp_path):
    path = _sealed(tmp_path, payloads=5)
    path.write_text(path.read_text().splitlines()[0] + "\n")
    reproduced, _report = reproduce_recorded_ticks(path, LOG)
    assert not reproduced


def test_the_replay_audit_holds_no_broker_and_builds_no_order():
    """It takes a path and returns a report, so it cannot trigger or repeat an
    order however it is called or scheduled. Asserted as AST, not substring."""
    import ast
    import inspect as _inspect

    import bujji.production_runtime.tick_evidence as mod

    tree = ast.parse(_inspect.getsource(mod))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "reproduce_recorded_ticks")
    called = {n.func.attr for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    called |= {n.func.id for n in ast.walk(fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    forbidden = {"place_order", "submit_and_confirm", "cancel_order", "modify_order",
                 "_execute_reduce", "build_reduce_order", "run_eod_closure"}
    assert not (called & forbidden), f"the audit reaches an order path: {called & forbidden}"


# --------------------------------------------------------------------------
# The terminal effect: what each verdict does to the session.
# --------------------------------------------------------------------------

_CLEAN_PRIOR = {"inspected": True, "unsealed": [], "corrupt": []}


def _verdict(**journal):
    summary = {"entry_filled": False, "prior_tick_journals": _CLEAN_PRIOR}
    if journal:
        summary["tick_journal"] = journal
    return evaluate_session_safety(summary)


def test_a_verified_journal_certifies_the_session():
    v = _verdict(sealed=True, faithful=True,
                 verified={"outcome": VERIFIED, "state": STATE_FAITHFUL},
                 replay={"reproduced": True})
    assert v.safe and not v.pending_evidence and v.certified


def test_a_journal_that_fails_verification_makes_the_session_unsafe():
    v = _verdict(sealed=True, faithful=True,
                 verified={"outcome": NOT_FAITHFUL, "state": STATE_CORRUPT,
                           "detail": "hash mismatch"},
                 replay={"reproduced": True})
    assert not v.safe
    assert any("does not verify" in r for r in v.reasons)


def test_a_replay_mismatch_makes_the_session_unsafe():
    v = _verdict(sealed=True, faithful=True,
                 verified={"outcome": VERIFIED, "state": STATE_FAITHFUL},
                 replay={"reproduced": False, "detail": "streams differ"})
    assert not v.safe


def test_an_unread_journal_is_pending_not_unsafe():
    """`faithful` alone is the writer's own count. Not wrong -- unproven."""
    v = _verdict(sealed=True, faithful=True)
    assert v.safe, "nothing is known to be wrong"
    assert v.pending_evidence and not v.certified


def test_a_corrupt_prior_journal_makes_this_session_unsafe():
    v = evaluate_session_safety({
        "entry_filled": False,
        "prior_tick_journals": {"inspected": True, "unsealed": [],
                                "corrupt": [{"path": "/x/old.jsonl"}]},
        "tick_journal": {"sealed": True, "faithful": True,
                         "verified": {"outcome": VERIFIED, "state": STATE_FAITHFUL},
                         "replay": {"reproduced": True}},
    })
    assert not v.safe
    assert any("CORRUPT" in r for r in v.reasons)


def test_an_uninspected_startup_is_pending():
    v = evaluate_session_safety({"entry_filled": False})
    assert v.safe and v.pending_evidence and not v.certified


def test_a_prior_unsealed_journal_alone_does_not_block_a_healthy_session():
    """An earlier crash is recorded, not punished. A session that never traded
    yesterday must not be unable to trade today."""
    v = evaluate_session_safety({
        "entry_filled": False,
        "prior_tick_journals": {"inspected": True, "corrupt": [],
                                "unsealed": [{"path": "/x/old.jsonl",
                                              "state": STATE_INCOMPLETE}]},
        "tick_journal": {"sealed": True, "faithful": True,
                         "verified": {"outcome": VERIFIED, "state": STATE_FAITHFUL},
                         "replay": {"reproduced": True}},
    })
    assert v.certified
