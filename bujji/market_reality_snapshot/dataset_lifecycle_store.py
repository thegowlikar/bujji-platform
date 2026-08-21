"""Dataset lifecycle event log — Phase 18.14.

Same WAL-SQLite pattern every other store in this project uses. A
`DatasetArtifact` record itself (Phase 18.12) stays exactly what it
already was: immutable, INSERT-only, never touched again after
creation ("do not mutate historical research content," this phase's
own explicit instruction). Lifecycle STATE is therefore modeled as a
SEPARATE, append-only EVENT LOG keyed by `artifact_id` -- the current
state is always DERIVED (the latest event for that id), never stored
as a mutable column anywhere. This is the same append-only-ledger
discipline `IngestionRun` (Phase 17H.2) and every conflict-guarded
store already use for a different kind of fact -- reused as a pattern,
not duplicated as code.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import List, Optional, Union


class DatasetLifecycleStore:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = str(path)
        directory = Path(self._path).parent
        if str(directory):
            directory.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dataset_lifecycle_events (
                event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                artifact_id  TEXT NOT NULL,
                occurred_at  TEXT NOT NULL,
                record       TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_lifecycle_events_artifact
                ON dataset_lifecycle_events (artifact_id, event_id);
            """
        )
        self._conn.commit()

    @property
    def path(self) -> str:
        return self._path

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "DatasetLifecycleStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _run(self, fn, attempts: int = 5, base_delay: float = 0.01):
        last = None
        for attempt in range(attempts):
            try:
                return fn()
            except sqlite3.OperationalError as exc:  # noqa: PERF203
                if "locked" not in str(exc).lower():
                    raise
                last = exc
                time.sleep(base_delay * (2 ** attempt))
        raise last

    def append(self, artifact_id: str, occurred_at: str, record: dict) -> None:
        """Always inserts a NEW row -- never checks for or prevents
        duplicates, because a real transition history legitimately
        replays the same states across genuinely different events (an
        append-only LOG, not a conflict-guarded FACT store like
        `HistoricalObservationStore`). Ordering/validity of the
        transition itself is enforced by the caller (`transition()` in
        `dataset_artifact.py`) BEFORE this is ever called -- this store
        only records what already passed that check."""
        def _txn():
            self._conn.execute(
                "INSERT INTO dataset_lifecycle_events (artifact_id, occurred_at, record) VALUES (?, ?, ?)",
                (artifact_id, occurred_at, json.dumps(record, sort_keys=True)),
            )
            self._conn.commit()
        self._run(_txn)

    def history_for(self, artifact_id: str) -> List[dict]:
        cur = self._conn.execute(
            "SELECT record FROM dataset_lifecycle_events WHERE artifact_id = ? ORDER BY event_id ASC",
            (artifact_id,),
        )
        return [json.loads(r["record"]) for r in cur.fetchall()]

    def latest_event_for(self, artifact_id: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT record FROM dataset_lifecycle_events WHERE artifact_id = ? "
            "ORDER BY event_id DESC LIMIT 1",
            (artifact_id,),
        )
        row = cur.fetchone()
        return json.loads(row["record"]) if row is not None else None
