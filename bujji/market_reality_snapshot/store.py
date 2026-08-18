"""Market Reality Snapshot store — Phase 17H.5. SQLite, WAL mode.

Same precedent as `HistoricalObservationStore`/`CandleStore`: own
file, own schema, WAL, retry-on-locked. No DuckDB, no Parquet, no new
dependency (Phase 17H.2 Part 3's storage decision, reused unchanged).

CACHE, NOT LEDGER -- stated explicitly because it differs from every
other store in this project: a snapshot for an in-progress trading day
legitimately changes as more live ticks arrive (a real, different
high/low/close each time it's recomputed). `is_final` (settled vs.
in-progress, from `models.MarketRealitySnapshot`) governs the write
rule:
  * `is_final=False` (today, still trading) -- always upsert. A
    different result on recomputation is expected, not a conflict.
  * `is_final=True` (a settled past day) -- same conflict discipline as
    every other reality store: identical content is an idempotent
    no-op, DIFFERENT content raises. A settled day's view should never
    legitimately change; if it does, that is a real finding (e.g. a
    late historical backfill correction), not something to silently
    overwrite.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional, Union

from .models import MarketRealitySnapshot

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_reality_snapshots (
    date        TEXT    NOT NULL PRIMARY KEY,
    completeness TEXT   NOT NULL,
    is_final    INTEGER NOT NULL,
    record      TEXT    NOT NULL
);
"""


class ConflictingSnapshotError(Exception):
    """A settled (`is_final=True`) day's snapshot already exists with
    DIFFERENT content. Never silently overwritten -- see module
    docstring."""


class MarketRealitySnapshotStore:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = str(path)
        directory = Path(self._path).parent
        if str(directory):
            directory.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @property
    def path(self) -> str:
        return self._path

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "MarketRealitySnapshotStore":
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

    def write(self, snapshot: MarketRealitySnapshot) -> bool:
        """Returns True if the stored row changed (new or updated
        in-progress day), False if an identical settled-day row already
        existed (idempotent no-op). Raises `ConflictingSnapshotError`
        for a settled day whose stored content differs."""
        record_json = json.dumps(snapshot.to_dict())

        def _txn():
            cur = self._conn.execute(
                "SELECT record, is_final FROM market_reality_snapshots WHERE date = ?",
                (snapshot.date,),
            )
            row = cur.fetchone()
            if row is not None:
                existing = json.loads(row["record"])
                existing_is_final = bool(row["is_final"])
                same_content = (
                    existing.get("spot") == snapshot.spot.to_dict() if snapshot.spot else existing.get("spot") is None
                ) and (
                    existing.get("futures") == snapshot.futures.to_dict() if snapshot.futures else existing.get("futures") is None
                ) and (
                    existing.get("vix") == snapshot.vix.to_dict() if snapshot.vix else existing.get("vix") is None
                )
                if existing_is_final and not same_content:
                    raise ConflictingSnapshotError(
                        f"date={snapshot.date!r} is a settled day whose stored snapshot "
                        "differs from this recomputation -- a real finding, never "
                        "silently overwritten."
                    )
                if existing_is_final and same_content:
                    return False  # Idempotent no-op.
                # in-progress day (or a settled day being finalized for the first
                # time): upsert freely.
            self._conn.execute(
                "INSERT OR REPLACE INTO market_reality_snapshots (date, completeness, is_final, record) "
                "VALUES (?, ?, ?, ?)",
                (snapshot.date, snapshot.completeness, int(snapshot.is_final), record_json),
            )
            self._conn.commit()
            return True

        return self._run(_txn)

    def get(self, date: str) -> Optional[MarketRealitySnapshot]:
        cur = self._conn.execute(
            "SELECT record FROM market_reality_snapshots WHERE date = ?", (date,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return MarketRealitySnapshot.from_dict(json.loads(row["record"]))

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS n FROM market_reality_snapshots").fetchone()["n"]

    def range(self, date_from: str, date_to: str):
        """Every stored snapshot in [date_from, date_to], inclusive,
        ordered by date. Phase 17H.6's `MarketRealityTimeline` reads
        through this -- the store's job stays persistence, the
        timeline's job is filtering."""
        cur = self._conn.execute(
            "SELECT record FROM market_reality_snapshots WHERE date >= ? AND date <= ? "
            "ORDER BY date ASC",
            (date_from, date_to),
        )
        return [MarketRealitySnapshot.from_dict(json.loads(r["record"])) for r in cur.fetchall()]
