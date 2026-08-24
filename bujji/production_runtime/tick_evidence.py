"""The production consumer of the tick journal's READ side.

WHY THIS EXISTS. M2 built a durable tick journal with a manifest and a
deterministic replay module that reports FAITHFUL / INCOMPLETE / CORRUPT and
gates reads by purpose. The WRITE side was wired -- the runner opens the
journal, the feed writes every payload verbatim, shutdown seals it. The READ
side was not: `read_journal`, `recover_unsealed`, `open_journal`, `payloads`
and `ReplayResult.require` had no production caller at all.

That is the defect class this whole campaign exists to remove -- a safety
capability that exists, is tested, and is never reached -- and M2 introduced
an instance of it. This module is the named consumer.

WHAT IT CHANGES, CONCRETELY. Before this, the session summary's
`tick_journal.faithful` was the WRITER'S SELF-REPORT: the journal saying "I
dropped nothing". Nothing ever opened the file back up. A journal truncated
after its last fsync, or corrupted on disk, or whose bytes no longer match the
checksum the manifest recorded, still reported `faithful: true` and the
session still exited 0. Verification now comes from reading the file.

THREE ENTRY POINTS, THREE CONSEQUENCES:

  inspect_prior_journals()   at STARTUP, before any entry. A journal left
                             unsealed by an earlier process is that process's
                             death certificate. Recorded in the evidence chain;
                             a CORRUPT one makes the session unsafe.
  verify_sealed_journal()    at SHUTDOWN, after sealing. Replaces the writer's
                             claim with a reader's verdict. A position opened
                             against a journal that does not verify makes the
                             session unsafe.
  reproduce_recorded_ticks() POST-SESSION, after all trading has ended. Reads
                             the sealed journal back and checks the recorded
                             input stream reproduces deterministically.

WHAT THIS DELIBERATELY IS NOT. It is not a second strategy runner. It never
holds a broker, never constructs an order, and never reaches an execution
path -- `reproduce_recorded_ticks` takes a file path and returns a report.
Replay here is an AUDIT of recorded inputs, not a re-run of decisions.

AND WHAT IT CANNOT YET BE. The stated goal is that replay proves recorded
inputs reproduce the recorded DECISION TRAIL. That cannot be built today
because no such trail is recorded: the trading runner writes a session summary
and a position-group journal, but nothing captures, per decision, the inputs
that produced it. Verifying inputs reproduce is the honest subset that is
available now; decision equivalence needs a decision trail first, and claiming
it without one would be exactly the kind of unearned certification this module
exists to prevent.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from bujji.tick_journal import (
    PURPOSE_RESEARCH, PURPOSE_SAFETY_CERTIFICATION,
    PURPOSE_SESSION_SUCCESS_EVIDENCE, STATE_CORRUPT, STATE_FAITHFUL,
    STATE_INCOMPLETE, JournalIntegrityError, JournalNotFaithfulError,
    open_journal, payloads, read_journal,
)

MANIFEST_SUFFIX = ".manifest.json"

# Verification outcomes, distinct from the journal's own state vocabulary.
VERIFIED = "VERIFIED"
UNVERIFIABLE = "UNVERIFIABLE"      # the reader could not reach a verdict
NOT_FAITHFUL = "NOT_FAITHFUL"      # the reader reached one, and it is bad


@dataclass
class PriorJournalEvidence:
    """What earlier processes left behind. Attached to the session evidence
    chain at startup, before any entry may proceed."""

    inspected: bool = False
    unsealed: List[Dict[str, Any]] = field(default_factory=list)
    corrupt: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def any_corrupt(self) -> bool:
        return bool(self.corrupt)

    def to_dict(self) -> Dict[str, Any]:
        return {"inspected": self.inspected, "unsealed": list(self.unsealed),
                "corrupt": list(self.corrupt), "error": self.error}


@dataclass
class SealedJournalEvidence:
    """A reader's verdict on this session's own journal."""

    outcome: str = UNVERIFIABLE
    state: Optional[str] = None
    records: int = 0
    detail: str = ""
    # The ReplayResult's own serialisation, kept verbatim so the evidence
    # package carries what the reader saw rather than this module's summary.
    replay: Optional[Dict[str, Any]] = None

    @property
    def verified_faithful(self) -> bool:
        return self.outcome == VERIFIED and self.state == STATE_FAITHFUL

    def to_dict(self) -> Dict[str, Any]:
        return {"outcome": self.outcome, "state": self.state,
                "records": self.records, "detail": self.detail,
                "replay_result": self.replay}


def _manifest_for(journal_path: pathlib.Path) -> pathlib.Path:
    return journal_path.with_suffix(MANIFEST_SUFFIX)


def inspect_prior_journals(journal_root, current_session_id: str, logger,
                           limit: int = 20) -> PriorJournalEvidence:
    """Every journal under `journal_root` that is NOT this session's, reported
    with the state a reader can actually establish.

    Never raises: this runs at startup, and a bookkeeping failure must not
    prevent a session from starting -- but `inspected` stays False if it could
    not run, so nothing downstream can mistake silence for a clean result.

    An unsealed journal means an earlier process died before it could seal:
    SIGKILL, power loss, an OOM kill. `recover_unsealed` reports INCOMPLETE by
    construction -- whatever survived is real, but no inspection of the file
    can establish that it is ALL of it.
    """
    evidence = PriorJournalEvidence()
    try:
        root = pathlib.Path(journal_root)
        if not root.exists():
            evidence.inspected = True
            return evidence
        found = sorted(p for p in root.rglob("*.jsonl")
                       if current_session_id not in p.name)[:limit]
        for path in found:
            manifest = _manifest_for(path)
            record = {"path": str(path), "sealed": manifest.exists()}
            try:
                # PURPOSE_RESEARCH, deliberately. The strict purposes make
                # `ReplayResult.require` RAISE on anything short of FAITHFUL,
                # and an unsealed journal is INCOMPLETE by construction -- so
                # certifying-purpose inspection would turn every ordinary crash
                # remnant into an exception, and this function would file it as
                # CORRUPT. Inspection OBSERVES the state; it does not demand
                # one. The severity distinction is made below, from the state.
                result = open_journal(path, manifest if manifest.exists() else None,
                                      purpose=PURPOSE_RESEARCH)
                record["state"] = result.state
                record["records"] = len(result.records)
                record["replay"] = result.as_dict()
            except Exception as exc:  # noqa: BLE001 -- an unreadable file is a finding
                record["state"] = STATE_CORRUPT
                record["error"] = f"{type(exc).__name__}: {exc}"
            if record["state"] == STATE_CORRUPT:
                evidence.corrupt.append(record)
                logger.critical(
                    "PRIOR TICK JOURNAL CORRUPT at %s (%s). An earlier session's "
                    "evidence cannot be read, so nothing about that session can "
                    "be reconstructed or certified.", path, record.get("error", ""))
            elif not record["sealed"]:
                evidence.unsealed.append(record)
                logger.warning(
                    "PRIOR TICK JOURNAL UNSEALED at %s -- state=%s, %s record(s). "
                    "An earlier process died before it could seal this. Recorded "
                    "in this session's evidence chain.",
                    path, record["state"], record.get("records"))
        evidence.inspected = True
    except Exception as exc:  # noqa: BLE001
        evidence.error = f"{type(exc).__name__}: {exc}"
        logger.critical(
            "PRIOR TICK JOURNAL INSPECTION FAILED (%s). Treated as evidence NOT "
            "established, never as evidence of nothing.", evidence.error)
    return evidence


def verify_sealed_journal(journal_path, logger,
                          purpose: str = PURPOSE_SESSION_SUCCESS_EVIDENCE
                          ) -> SealedJournalEvidence:
    """Open this session's sealed journal and verify it, at shutdown.

    THE POINT. `tick_journal.faithful` in the session summary was the WRITER
    stating its own drop count. This reads the file back through
    `read_journal`, which re-derives the content hash, checks it against the
    manifest, verifies the record sequence has no gaps, and compares the count
    against what the manifest said should be there. A journal that was
    truncated after its last fsync, or altered on disk, self-reports faithful
    and fails here.

    Never raises: this runs in teardown, where a failure must not replace the
    session's real result with its own.
    """
    evidence = SealedJournalEvidence()
    try:
        path = pathlib.Path(journal_path)
        manifest = _manifest_for(path)
        if not manifest.exists():
            evidence.detail = (f"no manifest at {manifest} -- the journal was never "
                               f"sealed, so there is nothing to verify it against")
            logger.critical("TICK JOURNAL VERIFICATION -- %s", evidence.detail)
            return evidence
        result = read_journal(path, manifest, purpose=purpose)
        evidence.state = result.state
        evidence.records = len(result.records)
        evidence.replay = result.as_dict()
        evidence.outcome = VERIFIED if result.state == STATE_FAITHFUL else NOT_FAITHFUL
        evidence.detail = (f"{result.state}: {evidence.records} record(s) verified "
                           f"against the manifest")
        logger.info("TICK JOURNAL VERIFIED -- %s", evidence.detail)
    except (JournalNotFaithfulError, JournalIntegrityError) as exc:
        # THE READER REACHED A VERDICT, AND IT IS BAD. `purpose` here is
        # strict, so `require` raises rather than returning a state -- and a
        # raise from THESE two types is a finding, not a failure to look. The
        # generic handler below would have filed it as UNVERIFIABLE, which the
        # session verdict treats as PENDING rather than UNSAFE. That is the
        # difference between "we could not check" and "we checked and the file
        # does not match its manifest".
        evidence.outcome = NOT_FAITHFUL
        evidence.detail = f"{type(exc).__name__}: {exc}"
        logger.critical(
            "TICK JOURNAL NOT FAITHFUL -- %s. The writer's own counters said "
            "otherwise; the file does not agree.", evidence.detail)
    except Exception as exc:  # noqa: BLE001 -- teardown must not raise
        evidence.outcome = UNVERIFIABLE
        evidence.detail = f"{type(exc).__name__}: {exc}"
        logger.critical(
            "TICK JOURNAL VERIFICATION FAILED -- %s. Evidence for this session is "
            "NOT established.", evidence.detail)
    return evidence


def reproduce_recorded_ticks(journal_path, logger) -> Tuple[bool, Dict[str, Any]]:
    """POST-SESSION. Read the sealed journal twice and prove the recorded input
    stream reproduces deterministically.

    (reproduced, report). Never raises.

    THIS IS AN AUDIT, NOT A RE-RUN. It takes a path and returns a report. It
    holds no broker, constructs no order, and reaches no execution path, so it
    cannot trigger or repeat an order however it is called or scheduled.

    SCOPE, STATED PLAINLY. This proves the recorded INPUTS reproduce. It does
    NOT prove they reproduce the recorded DECISION trail, because no decision
    trail with inputs is recorded anywhere in this system today. That is the
    prerequisite for decision-equivalence replay, and until it exists this
    function must not be described as providing it.
    """
    report: Dict[str, Any] = {"scope": "INPUT_REPRODUCTION_ONLY"}
    try:
        path = pathlib.Path(journal_path)
        manifest = _manifest_for(path)
        if not manifest.exists():
            report["detail"] = "unsealed -- nothing to reproduce against"
            return False, report
        first = read_journal(path, manifest, purpose=PURPOSE_SAFETY_CERTIFICATION)
        second = read_journal(path, manifest, purpose=PURPOSE_SAFETY_CERTIFICATION)
        a = [dict(p) for p in payloads(first)]
        b = [dict(p) for p in payloads(second)]
        report.update({"state": first.state, "records": len(a)})
        if first.state != STATE_FAITHFUL:
            report["detail"] = f"journal is {first.state}; inputs cannot be certified"
            return False, report
        if a != b:
            report["detail"] = ("two reads of the same sealed journal produced "
                                "different payload streams")
            logger.critical("TICK REPLAY MISMATCH -- %s", report["detail"])
            return False, report
        report["detail"] = f"{len(a)} recorded payload(s) reproduced identically"
        return True, report
    except (JournalNotFaithfulError, JournalIntegrityError) as exc:
        report["detail"] = f"{type(exc).__name__}: {exc}"
        logger.critical(
            "TICK REPLAY -- the sealed journal does not satisfy certification "
            "(%s). Recorded inputs cannot be certified to reproduce.",
            report["detail"])
        return False, report
    except Exception as exc:  # noqa: BLE001
        report["detail"] = f"{type(exc).__name__}: {exc}"
        logger.critical("TICK REPLAY VERIFICATION FAILED -- %s", report["detail"])
        return False, report
