"""Dataset Artifact Registry storage — Phase 18.12.

Same WAL-SQLite pattern every other store in this project already uses
(`HistoricalObservationStore`, `MarketRealitySnapshotStore`,
`market_timeseries.CandleStore`) -- own file, own schema, WAL,
retry-on-locked. No new storage technology, no external dependency, per
this phase's own explicit constraint.

IMMUTABLE, INSERT-ONLY -- an artifact record represents one frozen
creation event (PHASE_18_11's own finding: `artifact_id` must NOT be
regenerated). A second `write()` for an `artifact_id` that already
exists with DIFFERENT content raises, mirroring
`ConflictingHistoricalObservationError`'s own discipline; identical
content is an idempotent no-op. There is no update path and none is
added -- an artifact is a historical record of what was created, not a
mutable object.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import List, Optional, Union


class ConflictingDatasetArtifactError(Exception):
    """A DIFFERENT artifact record already exists under this
    `artifact_id`. Since `artifact_id` is minted fresh (a UUID) per
    creation event, this should only ever fire on a genuine bug (id
    reuse) -- never silently overwritten regardless."""


class DatasetArtifactStore:
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
            CREATE TABLE IF NOT EXISTS dataset_artifacts (
                artifact_id  TEXT NOT NULL PRIMARY KEY,
                dataset_id   TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                record       TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_dataset_artifacts_dataset_id
                ON dataset_artifacts (dataset_id);
            CREATE INDEX IF NOT EXISTS idx_dataset_artifacts_created_at
                ON dataset_artifacts (created_at);
            """
        )
        self._conn.commit()

    @property
    def path(self) -> str:
        return self._path

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "DatasetArtifactStore":
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

    def write(self, artifact_id: str, dataset_id: str, created_at: str, record: dict) -> bool:
        """Returns True if newly inserted, False if an identical record
        already existed (idempotent no-op). Raises
        `ConflictingDatasetArtifactError` if a DIFFERENT record already
        holds this `artifact_id`."""
        record_json = json.dumps(record, sort_keys=True)

        def _txn():
            cur = self._conn.execute(
                "SELECT record FROM dataset_artifacts WHERE artifact_id = ?", (artifact_id,),
            )
            row = cur.fetchone()
            if row is not None:
                if row["record"] == record_json:
                    return False
                raise ConflictingDatasetArtifactError(
                    f"artifact_id={artifact_id!r} already holds a different record -- "
                    "artifact identity must never be regenerated or overwritten."
                )
            self._conn.execute(
                "INSERT INTO dataset_artifacts (artifact_id, dataset_id, created_at, record) "
                "VALUES (?, ?, ?, ?)",
                (artifact_id, dataset_id, created_at, record_json),
            )
            self._conn.commit()
            return True

        return self._run(_txn)

    def get(self, artifact_id: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT record FROM dataset_artifacts WHERE artifact_id = ?", (artifact_id,),
        )
        row = cur.fetchone()
        return json.loads(row["record"]) if row is not None else None

    def list_for_dataset(self, dataset_id: str) -> List[dict]:
        cur = self._conn.execute(
            "SELECT record FROM dataset_artifacts WHERE dataset_id = ? ORDER BY created_at ASC",
            (dataset_id,),
        )
        return [json.loads(r["record"]) for r in cur.fetchall()]

    def list_all(self, limit: Optional[int] = None) -> List[dict]:
        sql = "SELECT record FROM dataset_artifacts ORDER BY created_at ASC"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        cur = self._conn.execute(sql, params)
        return [json.loads(r["record"]) for r in cur.fetchall()]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS n FROM dataset_artifacts").fetchone()["n"]
