"""Reading a tick journal for a stated PURPOSE, and refusing when it cannot serve it.

A JOURNAL IS FAITHFUL ONLY IF ALL OF THESE HOLD:

  * it carries a manifest, written atomically by `close()`;
  * the manifest's content hash matches the file's bytes;
  * the record count matches, and sequence numbers are contiguous from 1;
  * nothing was dropped at the queue and no write failed.

WHAT SEALING DOES NOT PROVE. `_shutdown()` runs from `run()`'s `finally`, which
covers orderly termination -- an exception, a SIGTERM, a refusal to trade. It
does NOT cover SIGKILL, power loss, or the container being torn out, and it
cannot: no handler runs. It also does not cover the CRASH TAIL -- records
written since the last fsync, which the OS had not yet committed. A journal
found without a manifest was therefore interrupted, and is INCOMPLETE by
definition rather than by inspection.

PURPOSE GATES THE ANSWER, because "can I read this?" and "may I conclude from
it?" are different questions:

  RESEARCH                  an incomplete journal is still evidence; read it
  DECISION_EQUIVALENCE      requires FAITHFUL -- a replay that omits records
                            reproduces a decision from different inputs and
                            calls the agreement meaningful
  SAFETY_CERTIFICATION      requires FAITHFUL
  SESSION_SUCCESS_EVIDENCE  requires FAITHFUL

Reading for a strict purpose raises. It does not warn, because a warning in a
log nobody greps is how missing evidence becomes apparent evidence.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .journal import SCHEMA_VERSION
from .manifest import JournalManifest

# Journal states. Only FAITHFUL may support a conclusion.
STATE_FAITHFUL = "FAITHFUL"
STATE_INCOMPLETE = "INCOMPLETE"      # readable, honestly short
STATE_CORRUPT = "CORRUPT"            # unreadable; never yields records

# Purposes.
PURPOSE_RESEARCH = "RESEARCH"
PURPOSE_DECISION_EQUIVALENCE = "DECISION_EQUIVALENCE"
PURPOSE_SAFETY_CERTIFICATION = "SAFETY_CERTIFICATION"
PURPOSE_SESSION_SUCCESS_EVIDENCE = "SESSION_SUCCESS_EVIDENCE"

_STRICT_PURPOSES = frozenset({
    PURPOSE_DECISION_EQUIVALENCE,
    PURPOSE_SAFETY_CERTIFICATION,
    PURPOSE_SESSION_SUCCESS_EVIDENCE,
})
ALL_PURPOSES = frozenset({PURPOSE_RESEARCH}) | _STRICT_PURPOSES


class JournalIntegrityError(Exception):
    """The journal cannot be read at all, or does not match its manifest."""


class JournalNotFaithfulError(Exception):
    """Readable, but not sound enough for the purpose asked of it."""


@dataclass(frozen=True)
class TickRecord:
    seq: int
    recv_epoch: float
    recv_monotonic: float
    payload: Dict[str, Any]


@dataclass(frozen=True)
class ReplayResult:
    records: Tuple[TickRecord, ...]
    manifest: Optional[JournalManifest]
    state: str
    reasons: Tuple[str, ...]
    sealed: bool
    discarded_partial_tail: bool = False

    @property
    def faithful(self) -> bool:
        return self.state == STATE_FAITHFUL

    def require(self, purpose: str) -> "ReplayResult":
        """Return self, or refuse if this journal cannot serve `purpose`."""
        if purpose not in ALL_PURPOSES:
            raise ValueError(f"unknown replay purpose {purpose!r}")
        if purpose in _STRICT_PURPOSES and not self.faithful:
            raise JournalNotFaithfulError(
                f"this journal is {self.state} and cannot support {purpose}: "
                + ("; ".join(self.reasons) or "no reason recorded")
                + ". It remains readable for RESEARCH.")
        return self

    def as_dict(self) -> Dict[str, Any]:
        return {
            "records": len(self.records), "state": self.state,
            "faithful": self.faithful, "sealed": self.sealed,
            "discarded_partial_tail": self.discarded_partial_tail,
            "reasons": list(self.reasons),
            "session_id": self.manifest.session_id if self.manifest else None,
            "max_crash_loss_records":
                self.manifest.max_crash_loss_records if self.manifest else None,
            "max_crash_loss_seconds":
                self.manifest.max_crash_loss_seconds if self.manifest else None,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse(path: Path, *, tolerate_partial_tail: bool) -> Tuple[List[TickRecord], bool]:
    """Records, and whether a partial final line was discarded.

    A partial FINAL line is the signature of a crash mid-write and is the only
    damage that may be tolerated -- and only when recovering an unsealed
    journal, where a crash is already established. A malformed line ANYWHERE
    ELSE means the file is corrupt, and is never skipped: skipping it would
    present partial evidence as complete.
    """
    raw_lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    discarded = False
    if tolerate_partial_tail and raw_lines and not raw_lines[-1].endswith("\n"):
        raw_lines = raw_lines[:-1]
        discarded = True

    records: List[TickRecord] = []
    for lineno, line in enumerate(raw_lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            if tolerate_partial_tail and lineno == len(raw_lines):
                discarded = True
                continue
            raise JournalIntegrityError(
                f"{path}:{lineno} is not valid JSON ({exc}) -- refusing to "
                f"replay a journal with an unreadable record rather than "
                f"skipping it") from exc
        if obj.get("v") != SCHEMA_VERSION:
            raise JournalIntegrityError(
                f"{path}:{lineno} carries schema {obj.get('v')!r}, this reader "
                f"understands {SCHEMA_VERSION!r}")
        records.append(TickRecord(
            seq=int(obj["seq"]), recv_epoch=float(obj["recv_epoch"]),
            recv_monotonic=float(obj["recv_monotonic"]), payload=obj["payload"]))
    return records, discarded


def _check_sequence(records: List[TickRecord], path: Path) -> None:
    expected = list(range(1, len(records) + 1))
    actual = [r.seq for r in records]
    if actual != expected:
        missing = sorted(set(expected) - set(actual))[:10]
        raise JournalIntegrityError(
            f"{path}: sequence numbers are not contiguous from 1 -- "
            f"{len(set(expected) - set(actual))} gap(s), first at {missing}. "
            f"Records were assigned a sequence and never reached disk, which "
            f"the counters alone would not reveal.")


def read_journal(journal_path: Path, manifest_path: Path,
                 purpose: str = PURPOSE_RESEARCH) -> ReplayResult:
    """Verify a SEALED journal, then read it for `purpose`."""
    journal_path, manifest_path = Path(journal_path), Path(manifest_path)
    if not Path(manifest_path).exists():
        raise JournalIntegrityError(
            f"no manifest at {manifest_path} -- this journal was never sealed. "
            f"Use `recover_unsealed()`, which reports it as INCOMPLETE rather "
            f"than reading it as though a crash had not happened.")

    manifest = JournalManifest.read(manifest_path)
    actual = _sha256(journal_path)
    if actual != manifest.content_sha256:
        raise JournalIntegrityError(
            f"journal content hash {actual} does not match the manifest's "
            f"{manifest.content_sha256} -- truncated, altered, or written by a "
            f"process that did not finish. Refusing to replay it.")

    records, _ = _parse(journal_path, tolerate_partial_tail=False)
    _check_sequence(records, journal_path)

    reasons: List[str] = []
    if manifest.dropped:
        reasons.append(f"{manifest.dropped} record(s) dropped at the queue during capture")
    if manifest.offered != manifest.written:
        reasons.append(f"offered {manifest.offered} but wrote {manifest.written}")
    if len(records) != manifest.written:
        raise JournalIntegrityError(
            f"journal holds {len(records)} records but its manifest claims "
            f"{manifest.written} were written")

    state = STATE_FAITHFUL if not reasons else STATE_INCOMPLETE
    return ReplayResult(records=tuple(records), manifest=manifest, state=state,
                        reasons=tuple(reasons), sealed=True).require(purpose)


def recover_unsealed(journal_path: Path,
                     purpose: str = PURPOSE_RESEARCH) -> ReplayResult:
    """Read a journal that has NO manifest. Always INCOMPLETE.

    This is the crash path: the process died before `close()` could seal the
    file, so nothing states what the journal should contain. Whatever survives
    is real -- it was written -- but it cannot be known to be ALL of it, and no
    inspection of the file can establish that. The state is INCOMPLETE by
    definition, not by evidence.

    A partial final record is discarded and reported. That is the expected
    shape of a kill mid-write, and it is the ONLY damage tolerated here.
    """
    journal_path = Path(journal_path)
    if not journal_path.exists():
        raise JournalIntegrityError(f"no journal at {journal_path}")

    records, discarded = _parse(journal_path, tolerate_partial_tail=True)
    _check_sequence(records, journal_path)

    reasons = [
        "journal has no manifest -- the session was interrupted before it "
        "could be sealed, so the true record count is unknown and unknowable",
    ]
    if discarded:
        reasons.append(
            "a partial final record was discarded -- the signature of a kill "
            "mid-write")
    reasons.append(
        "records written since the last fsync may be absent; the acknowledged "
        "crash-loss window is unknown here because it is stated in the manifest "
        "this journal does not have")

    return ReplayResult(records=tuple(records), manifest=None,
                        state=STATE_INCOMPLETE, reasons=tuple(reasons),
                        sealed=False, discarded_partial_tail=discarded
                        ).require(purpose)


def open_journal(journal_path: Path, manifest_path: Optional[Path] = None,
                 purpose: str = PURPOSE_RESEARCH) -> ReplayResult:
    """Read a journal whether or not it was sealed, never guessing which.

    The single entry point a caller should reach for: it looks for the
    manifest, and routes to sealed verification or crash recovery accordingly.
    An unsealed journal is NEVER silently treated as a complete replay source.
    """
    journal_path = Path(journal_path)
    manifest_path = Path(manifest_path) if manifest_path is not None \
        else journal_path.with_suffix(".manifest.json")
    if manifest_path.exists():
        return read_journal(journal_path, manifest_path, purpose=purpose)
    return recover_unsealed(journal_path, purpose=purpose)


def payloads(result: ReplayResult) -> Iterator[Dict[str, Any]]:
    """The raw payloads, in arrival order -- exactly what `on_message` saw."""
    for record in result.records:
        yield record.payload
