"""What a journal claims about itself, written beside it and verified on read.

A journal file alone cannot say whether it is the WHOLE session. Truncation by
a kill -9, a burst that outran the writer, a disk that filled -- all leave a
file that parses perfectly and is silently short. The manifest is what turns
"this file exists" into "this file is a faithful record, or here is exactly how
it is not".

FAIL CLOSED ON READ. A manifest that does not match its journal is not a
warning, it is a refusal: a replay presented as faithful when it is not would
launder missing evidence into apparent evidence, which is the one thing an
evidence layer must never do.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

MANIFEST_VERSION = "tick-journal-manifest/1"


@dataclass(frozen=True)
class JournalManifest:
    version: str
    session_id: str
    as_of_date: str
    journal_filename: str
    started_at: str
    ended_at: Optional[str]
    # Accounting, copied from the journal's own counters at close.
    offered: int
    written: int
    dropped: int
    max_queue_depth: int
    bytes_written: int
    content_sha256: str
    # THE DURABILITY POLICY, AND THE LOSS IT ACKNOWLEDGES.
    #
    # Records are flushed to the OS on every write but fsync'd only every
    # `fsync_every_records` or `fsync_every_seconds`, whichever comes first.
    # Between syncs, a SIGKILL or power loss discards whatever the OS had not
    # yet written. That window is a PROPERTY OF THE JOURNAL, not a footnote,
    # so it is carried here: a reader must be able to state what a crash could
    # have cost without inspecting the writer's source.
    fsync_every_records: int
    fsync_every_seconds: float
    max_queue: int
    # What was intended to be captured, so a reader can tell "quiet symbol"
    # from "never subscribed" without re-deriving the universe.
    universe_symbols: int = 0
    notes: tuple = field(default_factory=tuple)

    @property
    def max_crash_loss_records(self) -> int:
        """Most records a SIGKILL could discard, by the record bound."""
        return int(self.fsync_every_records)

    @property
    def max_crash_loss_seconds(self) -> float:
        """Longest span a SIGKILL could discard, by the time bound."""
        return float(self.fsync_every_seconds)

    @property
    def complete(self) -> bool:
        """No record was dropped at the queue and none failed to write.

        NOTE what this does NOT assert: that the file survived a crash. A
        manifest exists only because `close()` ran, so its presence already
        implies an orderly shutdown -- an UNSEALED journal has no manifest at
        all and is handled by `recover_unsealed`, never by this flag.
        """
        return self.dropped == 0 and self.offered == self.written

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["complete"] = self.complete
        out["max_crash_loss_records"] = self.max_crash_loss_records
        out["max_crash_loss_seconds"] = self.max_crash_loss_seconds
        out["notes"] = list(self.notes)
        return out

    def write(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.as_dict(), indent=2, sort_keys=True),
                       encoding="utf-8")
        # Atomic replace: a manifest half-written by a crash would be worse
        # than none, because it would look authoritative.
        tmp.replace(path)

    @classmethod
    def read(cls, path: Path) -> "JournalManifest":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for derived in ("complete", "max_crash_loss_records", "max_crash_loss_seconds"):
            data.pop(derived, None)
        data["notes"] = tuple(data.get("notes") or ())
        known = {f for f in cls.__dataclass_fields__}          # noqa: SLF001
        unknown = set(data) - known
        if unknown:
            raise ValueError(
                f"manifest {path} carries unknown fields {sorted(unknown)} -- "
                f"refusing to read a manifest this version does not understand")
        return cls(**data)
