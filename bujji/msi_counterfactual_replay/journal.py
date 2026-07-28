"""CRE journal — Series 102. Append-only JSONL, mirrors
bujji.live_shadow_operator.journal.OperatorJournal's persistence
discipline (failed_writes tracked, never silently swallowed). Every
CounterfactualSession is recorded exactly once it is built -- including
ILLEGAL/acausal ones, disclosed honestly, never discarded."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from .models import CounterfactualSession
from .serialization import session_from_dict, session_to_dict


class CounterfactualReplayJournal:
    def __init__(self, directory: Path, logger: Optional[logging.Logger] = None) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "counterfactual_sessions.jsonl"
        self._log = logger or logging.getLogger("bujji.msi_counterfactual_replay.journal")
        self.failed_writes: int = 0

    def record(self, session: CounterfactualSession) -> None:
        entry = session_to_dict(session)
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001 -- persistence must never crash the caller
            self.failed_writes += 1
            self._log.error("cre_journal_write_failed session_id=%s failed_writes=%d: %s",
                            session.session_id, self.failed_writes, exc, exc_info=True)

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

    def by_id(self, session_id: str) -> Optional[CounterfactualSession]:
        for entry in self._iter_entries():
            if entry["session_id"] == session_id:
                return session_from_dict(entry)
        return None

    def all(self) -> List[CounterfactualSession]:
        return [session_from_dict(e) for e in self._iter_entries()]
