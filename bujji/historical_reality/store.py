"""Historical Market Reality store -- Phase 17H.3/17H.4. SQLite, WAL mode.

Follows the exact same precedent `market_timeseries.CandleStore` already
established (itself following `mil_next/snapshot_journal.py`/
`journal.py`): same WAL journal mode, same retry-on-locked helper shape,
own file, own schema. PHASE_17H2 Part 3's storage-engine decision
(SQLite now, DuckDB only at a measured future trigger) is implemented
here -- not DuckDB, not Parquet, no new dependency.

IMMUTABILITY / CONFLICT DISCIPLINE (PHASE_17H3 §3.1, mirroring
`RawObservationStore`/`CandleStore`'s own three-outcome pattern):
* A new `observation_id` -> inserted.
* An `observation_id` already present -> idempotent no-op (re-ingesting
  the identical fact, e.g. a re-run of the same chunk, is always safe).
* A DIFFERENT `observation_id` under the same natural key
  (instrument_identity, resolution, timestamp, source) -> a genuine
  conflict (the source returned a different value for a date already
  ingested) -- raised, never silently overwritten.

Two tables: `historical_observations` (the reality itself) and
`ingestion_runs` (PHASE_17H2 §2.3 -- one row per real fetch attempt, so
`NO_DATA` vs `ERROR` vs a normal run with no bar for one date remain
three distinguishable facts, never collapsed).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import List, Optional, Union

from .models import HistoricalObservation, IngestionRun

_SCHEMA = """
CREATE TABLE IF NOT EXISTS historical_observations (
    observation_id          TEXT    NOT NULL PRIMARY KEY,
    instrument_identity     TEXT    NOT NULL,
    instrument_type         TEXT    NOT NULL,
    resolution               TEXT    NOT NULL,
    timestamp                TEXT    NOT NULL,
    source                   TEXT    NOT NULL,
    payload                  TEXT    NOT NULL,
    record                   TEXT    NOT NULL,
    natural_key               TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hist_obs_natural_key
    ON historical_observations (natural_key);
CREATE INDEX IF NOT EXISTS idx_hist_obs_range
    ON historical_observations (instrument_identity, resolution, timestamp);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    ingestion_run_id  TEXT    NOT NULL PRIMARY KEY,
    instrument        TEXT    NOT NULL,
    started_at        TEXT    NOT NULL,
    record            TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_instrument
    ON ingestion_runs (instrument, started_at);
"""


def _natural_key(instrument_identity: str, resolution: str, timestamp: str, source: str) -> str:
    return "|".join((instrument_identity, resolution, timestamp, source))


class ConflictingHistoricalObservationError(Exception):
    """A DIFFERENT observation_id already exists for the same
    (instrument_identity, resolution, timestamp, source) natural key --
    the source returned a different value for an already-ingested date.
    Never silently overwritten; a human must review it (PHASE_17H3
    §3.1)."""


class HistoricalObservationStore:
    """One SQLite file. Safe to construct repeatedly against the same
    path (including across a process restart) -- never truncates."""

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

    def __enter__(self) -> "HistoricalObservationStore":
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

    # -- Write ------------------------------------------------------------ #
    def write(self, obs: HistoricalObservation) -> bool:
        """Returns True if a new row was inserted, False if an
        identical row already existed (idempotent no-op). Raises
        `ConflictingHistoricalObservationError` if a different fact
        already occupies this natural key."""
        nkey = _natural_key(obs.instrument, obs.observation.identity.resolution,
                             obs.observation.identity.timestamp, obs.lineage.source)

        def _txn():
            cur = self._conn.execute(
                "SELECT observation_id FROM historical_observations WHERE natural_key = ?",
                (nkey,),
            )
            row = cur.fetchone()
            if row is not None:
                if row["observation_id"] == obs.observation_id:
                    return False  # Idempotent no-op -- identical fact re-ingested.
                raise ConflictingHistoricalObservationError(
                    f"natural_key={nkey!r} already holds observation_id="
                    f"{row['observation_id']!r}, cannot also hold {obs.observation_id!r}. "
                    "A source correction must be reviewed explicitly, never overwritten."
                )
            self._conn.execute(
                "INSERT INTO historical_observations "
                "(observation_id, instrument_identity, instrument_type, resolution, "
                " timestamp, source, payload, record, natural_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    obs.observation_id, obs.instrument, obs.instrument_type,
                    obs.observation.identity.resolution, obs.observation.identity.timestamp,
                    obs.lineage.source, json.dumps(obs.payload), json.dumps(obs.to_dict()), nkey,
                ),
            )
            self._conn.commit()
            return True

        return self._run(_txn)

    def write_many(self, observations) -> List[bool]:
        return [self.write(o) for o in observations]

    def record_ingestion_run(self, run: IngestionRun) -> None:
        """Append-only. A run once recorded is never mutated in place --
        a completing run writes a NEW record (same id, later
        completed_at) only via `write_ingestion_run` being called once
        per real attempt; callers should construct a fresh IngestionRun
        with the final status rather than trying to update one."""
        def _txn():
            self._conn.execute(
                "INSERT OR REPLACE INTO ingestion_runs (ingestion_run_id, instrument, started_at, record) "
                "VALUES (?, ?, ?, ?)",
                (run.ingestion_run_id, run.instrument, run.started_at, json.dumps(run.to_dict())),
            )
            self._conn.commit()
        self._run(_txn)

    # -- Read ------------------------------------------------------------- #
    def holds(self, observation_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM historical_observations WHERE observation_id = ?", (observation_id,),
        )
        return cur.fetchone() is not None

    def count(self, instrument_identity: Optional[str] = None) -> int:
        if instrument_identity is None:
            cur = self._conn.execute("SELECT COUNT(*) AS n FROM historical_observations")
        else:
            cur = self._conn.execute(
                "SELECT COUNT(*) AS n FROM historical_observations WHERE instrument_identity = ?",
                (instrument_identity,),
            )
        return cur.fetchone()["n"]

    def range(self, instrument_identity: str, resolution: str,
              from_timestamp: str, to_timestamp: str) -> List[HistoricalObservation]:
        cur = self._conn.execute(
            "SELECT record FROM historical_observations "
            "WHERE instrument_identity = ? AND resolution = ? "
            "AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC",
            (instrument_identity, resolution, from_timestamp, to_timestamp),
        )
        return [HistoricalObservation.from_dict(json.loads(r["record"])) for r in cur.fetchall()]

    def ingestion_runs_for(self, instrument: str) -> List[IngestionRun]:
        cur = self._conn.execute(
            "SELECT record FROM ingestion_runs WHERE instrument = ? ORDER BY started_at ASC",
            (instrument,),
        )
        return [IngestionRun.from_dict(json.loads(r["record"])) for r in cur.fetchall()]
