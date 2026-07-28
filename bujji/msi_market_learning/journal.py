"""MLE journal — Series 100, Phase 1.0: the Trading Knowledge Base's
real, append-only persistence. Mirrors `bujji.live_shadow_operator.
journal.OperatorJournal` exactly (same failed_writes tracking, same
never-overwrite-in-place discipline) rather than inventing a new
persistence model."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from .models import KnowledgeCandidate
from .serialization import candidate_from_dict, candidate_to_dict


class KnowledgeBaseJournal:
    """Append-only JSONL. One line per real Knowledge Candidate write
    (new candidate, occurrence recorded, or lifecycle transition -- each
    call to `record` persists the candidate's CURRENT full state, so the
    journal is always replayable to the latest real state without
    needing to diff prior lines)."""

    def __init__(self, directory: Path, logger: Optional[logging.Logger] = None) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "knowledge_base_journal.jsonl"
        self._log = logger or logging.getLogger("bujji.msi_market_learning.journal")
        self.failed_writes: int = 0

    def record(self, candidate: KnowledgeCandidate) -> None:
        entry = candidate_to_dict(candidate)
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001 -- persistence must never crash the caller
            self.failed_writes += 1
            self._log.error("mle_journal_write_failed candidate_id=%s failed_writes=%d: %s",
                            candidate.candidate_id, self.failed_writes, exc, exc_info=True)

    def _iter_entries(self):
        if not self._path.exists():
            return
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def latest_state(self, candidate_id: str) -> Optional[KnowledgeCandidate]:
        """The most recently journaled state for a real candidate id --
        real restart recovery, same pattern as OperatorJournal's own
        `read_last_closes_with_ts`."""
        last = None
        for entry in self._iter_entries():
            if entry.get("candidate_id") == candidate_id:
                last = entry
        if last is None:
            return None
        return candidate_from_dict(last)

    def all_candidate_ids(self) -> List[str]:
        return sorted({entry["candidate_id"] for entry in self._iter_entries()})
