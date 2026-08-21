"""Futures Statistics store -- Phase 17F.1.2. SQLite, WAL mode.

Q3 (decided): a SEPARATE file from `CandleStore` --
`bujji/market_timeseries/futures_stats.db`, not `candles.db`. Matches
this codebase's one-store-per-file convention (RawObservationStore's
accepted/rejected JSONL, CandleStore's own file, outcome_memory,
portfolio_intelligence, ...) and keeps each store's migrations/rebuilds
from ever touching the other's table.

Structurally identical PATTERN to `CandleStore` (same PK shape --
`(instrument, interval, window_start, calc_version)` -- same
idempotent-write/raise-on-conflict discipline, same mandatory
`as_of`+`calc_version` on multi-row reads, same WAL/retry-on-locked
helper) -- reused in pattern per the audit's own finding, not
reimplemented from scratch, but NOT the same table: `FuturesStatistics`
and `Candle` are different schemas for different record types that
merely share a windowing convention.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Iterable, List, Optional, Union

from .futures_stats_models import FuturesStatistics
from bujji.epistemics.uncertainty import Uncertainty

_SCHEMA = """
CREATE TABLE IF NOT EXISTS futures_statistics (
    instrument               TEXT    NOT NULL,
    interval                 TEXT    NOT NULL,
    window_start              TEXT    NOT NULL,
    window_end                TEXT    NOT NULL,
    calc_version              TEXT    NOT NULL DEFAULT '',
    source_observation_ids    TEXT    NOT NULL DEFAULT '[]',
    referenced_candle_keys    TEXT    NOT NULL DEFAULT '[]',
    materializer_id           TEXT    NOT NULL DEFAULT '',
    transformation_history    TEXT    NOT NULL DEFAULT '[]',
    first_event_time          TEXT,
    last_event_time           TEXT,
    knowledge_boundary        TEXT,
    capture_event_overlap     TEXT    NOT NULL DEFAULT '[]',
    schema_version             TEXT    NOT NULL,
    oi_open                    REAL,
    oi_close                   REAL,
    oi_change                  REAL,
    oi_observation_count       INTEGER NOT NULL DEFAULT 0,
    depth_observation_count    INTEGER NOT NULL DEFAULT 0,
    volume                     REAL,
    price_change                REAL,
    book_state                  TEXT    NOT NULL,
    top_bid_size_last            REAL,
    top_ask_size_last             REAL,
    basis                         REAL,
    basis_percent                 REAL,
    price_volatility               REAL,
    quality                        TEXT    NOT NULL DEFAULT '{}',
    PRIMARY KEY (instrument, interval, window_start, calc_version)
);
CREATE INDEX IF NOT EXISTS idx_futures_stats_range
    ON futures_statistics (instrument, interval, calc_version, window_start DESC);
"""


class ConflictingFuturesStatisticsError(Exception):
    """Raised when a DIFFERENT record already exists for the same
    (instrument, interval, window_start, calc_version). Never silently
    overwritten -- mirrors `CandleStore.ConflictingCandleError` exactly:
    since `calc_version` is part of the key, this can only mean the SAME
    calculation, over the SAME window, produced different content."""


class FuturesStatsStore:
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

    def __enter__(self) -> "FuturesStatsStore":
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

    # -- Write ----------------------------------------------------------- #
    def write(self, stats: FuturesStatistics) -> bool:
        """Returns True if newly inserted, False if an IDENTICAL record
        already existed. Raises `ConflictingFuturesStatisticsError` if a
        DIFFERENT record exists for the same
        (instrument, interval, window_start, calc_version) key."""
        existing = self.get(
            stats.instrument, stats.interval, stats.window_start, stats.calc_version
        )
        if existing is not None:
            if existing.to_dict() == stats.to_dict():
                return False
            raise ConflictingFuturesStatisticsError(
                f"futures statistics already exist for {stats.instrument} {stats.interval} "
                f"{stats.window_start} calc_version={stats.calc_version!r} with "
                f"different content; history is immutable"
            )

        def _txn():
            with self._conn:
                self._conn.execute(
                    "INSERT INTO futures_statistics ("
                    "instrument, interval, window_start, window_end, calc_version, "
                    "source_observation_ids, referenced_candle_keys, materializer_id, "
                    "transformation_history, first_event_time, last_event_time, "
                    "knowledge_boundary, capture_event_overlap, schema_version, "
                    "oi_open, oi_close, oi_change, oi_observation_count, "
                    "depth_observation_count, volume, price_change, book_state, "
                    "top_bid_size_last, top_ask_size_last, basis, basis_percent, "
                    "price_volatility, quality) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        stats.instrument, stats.interval, stats.window_start, stats.window_end,
                        stats.calc_version,
                        json.dumps(list(stats.source_observation_ids)),
                        json.dumps([list(k) for k in stats.referenced_candle_keys]),
                        stats.materializer_id,
                        json.dumps(list(stats.transformation_history)),
                        stats.first_event_time, stats.last_event_time,
                        stats.knowledge_boundary,
                        json.dumps(list(stats.capture_event_overlap)),
                        stats.schema_version,
                        stats.oi_open, stats.oi_close, stats.oi_change,
                        stats.oi_observation_count, stats.depth_observation_count,
                        stats.volume, stats.price_change, stats.book_state,
                        stats.top_bid_size_last, stats.top_ask_size_last,
                        stats.basis, stats.basis_percent, stats.price_volatility,
                        json.dumps(stats.quality.to_dict()),
                    ),
                )
            return True

        return self._run(_txn)

    def write_many(self, records: Iterable[FuturesStatistics]) -> int:
        return sum(1 for r in records if self.write(r))

    # -- Read ------------------------------------------------------------ #
    @staticmethod
    def _row_to_stats(row: sqlite3.Row) -> FuturesStatistics:
        return FuturesStatistics(
            instrument=row["instrument"], interval=row["interval"],
            window_start=row["window_start"], window_end=row["window_end"],
            source_observation_ids=tuple(json.loads(row["source_observation_ids"] or "[]")),
            referenced_candle_keys=tuple(
                tuple(k) for k in json.loads(row["referenced_candle_keys"] or "[]")
            ),
            materializer_id=row["materializer_id"] or "",
            calc_version=row["calc_version"] or "",
            transformation_history=tuple(json.loads(row["transformation_history"] or "[]")),
            first_event_time=row["first_event_time"], last_event_time=row["last_event_time"],
            knowledge_boundary=row["knowledge_boundary"],
            capture_event_overlap=tuple(json.loads(row["capture_event_overlap"] or "[]")),
            schema_version=row["schema_version"],
            oi_open=row["oi_open"], oi_close=row["oi_close"], oi_change=row["oi_change"],
            oi_observation_count=row["oi_observation_count"],
            depth_observation_count=row["depth_observation_count"],
            volume=row["volume"], price_change=row["price_change"],
            book_state=row["book_state"],
            top_bid_size_last=row["top_bid_size_last"], top_ask_size_last=row["top_ask_size_last"],
            basis=row["basis"], basis_percent=row["basis_percent"],
            price_volatility=row["price_volatility"],
            quality=Uncertainty.from_dict(json.loads(row["quality"] or "{}")) if row["quality"] else Uncertainty(),
        )

    def get(
        self, instrument: str, interval: str, window_start: str, calc_version: str = ""
    ) -> Optional[FuturesStatistics]:
        row = self._conn.execute(
            "SELECT * FROM futures_statistics WHERE instrument=? AND interval=? "
            "AND window_start=? AND calc_version=?",
            (instrument, interval, window_start, calc_version),
        ).fetchone()
        return self._row_to_stats(row) if row is not None else None

    def recent(
        self, instrument: str, interval: str, count: int, *, as_of: str, calc_version: str
    ) -> List[FuturesStatistics]:
        """Mirrors `CandleStore.recent()` exactly: `as_of` and
        `calc_version` are REQUIRED keyword-only, no defaults. `as_of`
        bounds against `window_end` (a window's statistics are only
        knowable once the window has closed)."""
        rows = self._conn.execute(
            "SELECT * FROM futures_statistics WHERE instrument=? AND interval=? "
            "AND calc_version=? AND window_end <= ? ORDER BY window_start DESC LIMIT ?",
            (instrument, interval, calc_version, as_of, count),
        ).fetchall()
        return [self._row_to_stats(r) for r in reversed(rows)]

    def range(
        self, instrument: str, interval: str, start: str, end: str, *, as_of: str, calc_version: str
    ) -> List[FuturesStatistics]:
        rows = self._conn.execute(
            "SELECT * FROM futures_statistics WHERE instrument=? AND interval=? "
            "AND calc_version=? AND window_start >= ? AND window_start < ? AND window_end <= ? "
            "ORDER BY window_start ASC",
            (instrument, interval, calc_version, start, end, as_of),
        ).fetchall()
        return [self._row_to_stats(r) for r in rows]

    def calc_versions(self, instrument: str, interval: str) -> List[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT calc_version FROM futures_statistics WHERE instrument=? AND interval=? "
            "ORDER BY calc_version",
            (instrument, interval),
        ).fetchall()
        return [r["calc_version"] for r in rows]

    def count(self, instrument: Optional[str] = None) -> int:
        if instrument is None:
            return self._conn.execute("SELECT COUNT(*) AS n FROM futures_statistics").fetchone()["n"]
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM futures_statistics WHERE instrument=?", (instrument,)
        ).fetchone()["n"]
