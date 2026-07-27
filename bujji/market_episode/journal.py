"""Market Episode Engine journal — append-only, deterministic recorder
for Episode snapshots (creation, growth, transition, closure).

Mirrors `bujji.live_market_events.journal.MarketEventJournal`'s
established pattern exactly: append-only JSONL, own schema, own
storage path, entirely independent of every other journal in the
codebase. Never modifies or deletes a recorded snapshot -- growth and
closure both append a NEW record, never rewrite a prior line
(structural proof this journal enforces, not just documents: see
`tests/test_market_episode_engine.py::test_journal_append_only_structurally`,
which asserts the file's prior bytes are an exact prefix of the file's
bytes after any subsequent record_episode call).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .models import Episode
from .serialization import episode_from_dict, episode_to_dict

SCHEMA_VERSION = "1.0.0"


class MarketEpisodeJournal:
    """Append-only JSONL journal of Episode snapshots. Never modifies
    or deletes a previously-written record."""

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record_episode(self, episode: Episode) -> None:
        self._append({"schema_version": SCHEMA_VERSION, "kind": "EPISODE_SNAPSHOT", "episode": episode_to_dict(episode)})

    def _append(self, payload: dict) -> None:
        with open(self._path, "a") as fh:
            fh.write(json.dumps(payload, sort_keys=True, default=repr) + "\n")

    def read_all(self) -> List[dict]:
        if not self._path.exists():
            return []
        out: List[dict] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
        return out

    def read_episode_snapshots(self) -> List[Episode]:
        """Convenience: read_all() filtered/decoded back into Episode
        objects, in recorded (append) order -- may contain multiple
        snapshots per episode_id (one per growth/transition event)."""
        return [
            episode_from_dict(record["episode"])
            for record in self.read_all()
            if record.get("kind") == "EPISODE_SNAPSHOT"
        ]
