"""Replay-only snapshot journal -- BUJJI MIL Next.

Mirrors bujji.journal.position_group_journal's proven pattern (BEGIN
IMMEDIATE-serialized writes, DB-enforced idempotency-key uniqueness,
canonical-content collision detection) as its OWN, separate SQLite
store -- does not import, touch, or share storage with
position_group_journal.py or any Gate A file.

Idempotency: `idempotency_key = MIL:{session_id}:{cadence_id}:{decision_cutoff_event_time}`
is content-INDEPENDENT. A write under an existing key with a matching
content_hash is a no-op replay. A write under an existing key with a
DIFFERING content_hash raises IdempotencyCollisionError -- unless a
MILSnapshotRevision is supplied, in which case a NEW, separate,
additional journal row is appended referencing the original; the
original snapshot is never mutated or replaced.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Union

from .canonical_hash import compute_content_hash
from .models import MarketIntelligenceSnapshot, MILSnapshotRevision

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mil_snapshots (
    idempotency_key TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mil_snapshot_revisions (
    revision_id TEXT PRIMARY KEY,
    original_idempotency_key TEXT NOT NULL,
    revision_reason TEXT NOT NULL,
    revised_at TEXT NOT NULL,
    superseding_content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


class IdempotencyCollisionError(RuntimeError):
    def __init__(self, idempotency_key: str, existing_content_hash: str, proposed_content_hash: str) -> None:
        self.idempotency_key = idempotency_key
        self.existing_content_hash = existing_content_hash
        self.proposed_content_hash = proposed_content_hash
        super().__init__(
            f"idempotency_key {idempotency_key!r} collision: "
            f"existing content_hash={existing_content_hash!r}, proposed={proposed_content_hash!r}"
        )


def _to_jsonable_snapshot(snapshot: MarketIntelligenceSnapshot) -> dict:
    from .canonical_hash import _to_jsonable
    return _to_jsonable(snapshot)


class MILSnapshotJournal:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), isolation_level=None, timeout=5.0, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA_SQL)

    def close(self) -> None:
        self._conn.close()

    def _run(self, fn, attempts=5, base_delay=0.01):
        last = None
        for attempt in range(attempts):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                last = exc
                time.sleep(base_delay * (2 ** attempt))
        raise last

    def record(self, snapshot: MarketIntelligenceSnapshot) -> Optional[MarketIntelligenceSnapshot]:
        """Returns the snapshot on a fresh insert, None on an exact-replay
        no-op. Raises IdempotencyCollisionError on a genuine collision."""
        payload = _to_jsonable_snapshot(snapshot)
        proposed_hash = snapshot.content_hash

        def _txn():
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT content_hash FROM mil_snapshots WHERE idempotency_key = ?",
                    (snapshot.idempotency_key,),
                ).fetchone()
                if row is not None:
                    self._conn.execute("ROLLBACK")
                    if row[0] == proposed_hash:
                        return None
                    raise IdempotencyCollisionError(snapshot.idempotency_key, row[0], proposed_hash)
                self._conn.execute(
                    "INSERT INTO mil_snapshots (idempotency_key, content_hash, payload_json, recorded_at) "
                    "VALUES (?, ?, ?, ?)",
                    (snapshot.idempotency_key, proposed_hash, json.dumps(payload, sort_keys=True),
                     snapshot.data_quality.as_of.isoformat()),
                )
                self._conn.execute("COMMIT")
                return snapshot
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        with self._lock:
            return self._run(_txn)

    def record_revision(
        self, revision: MILSnapshotRevision, revised_snapshot: MarketIntelligenceSnapshot,
    ) -> None:
        if not revision.revision_reason or not revision.revision_reason.strip():
            raise ValueError("revision_reason must be non-empty")
        payload = _to_jsonable_snapshot(revised_snapshot)

        def _txn():
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "INSERT INTO mil_snapshot_revisions "
                    "(revision_id, original_idempotency_key, revision_reason, revised_at, "
                    " superseding_content_hash, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (revision.revision_id, revision.original_idempotency_key, revision.revision_reason,
                     revision.revised_at.isoformat(), revision.superseding_content_hash,
                     json.dumps(payload, sort_keys=True)),
                )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        with self._lock:
            self._run(_txn)

    def read(self, idempotency_key: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json, content_hash FROM mil_snapshots WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        return {"payload": json.loads(row[0]), "content_hash": row[1]}

    def read_revisions(self, original_idempotency_key: str) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT revision_id, revision_reason, revised_at, superseding_content_hash "
                "FROM mil_snapshot_revisions WHERE original_idempotency_key = ? ORDER BY revised_at ASC",
                (original_idempotency_key,),
            ).fetchall()
        return [
            {"revision_id": r[0], "revision_reason": r[1], "revised_at": r[2], "superseding_content_hash": r[3]}
            for r in rows
        ]
