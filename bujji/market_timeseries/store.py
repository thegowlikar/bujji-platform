"""Market Timeseries candle store -- Phase 15Q, provenance added 17F.0/17F.1.
SQLite, WAL mode.

Follows the codebase's existing SQLite precedent (`bujji/mil_next/
snapshot_journal.py`, `bujji/journal/journal.py`) rather than inventing
a storage pattern: same WAL journal mode, same retry-on-locked helper
shape, same "own file, own schema, independent of every other store"
discipline.

Why SQLite and not the existing EventStore JSONL: the EventStore is
optimised for append-only replay of a bounded session's events. A
candle store must answer indexed RANGE queries ("last 200 five-minute
bars for this strike") cheaply and across many sessions/days. That is a
genuinely different access pattern, and forcing it into JSONL would
mean a linear scan of every historical file per indicator evaluation.
Both stores coexist; neither replaces the other.

IMMUTABILITY: a candle is historical fact. `write_candle` is idempotent
for identical content and RAISES on conflicting content for the same
(instrument, interval, window_start, calc_version). History is never
silently rewritten -- same rule as Outcome Memory (Phase 15N).

PRIMARY KEY CHANGE (operator decision, 17F.0 Part 2.4): `calc_version`
joined the primary key. A re-materialization under a CHANGED aggregation
rule now produces a distinct, comparable row instead of colliding with
the old one -- `ConflictingCandleError` now fires only when the SAME
calc_version, same window, produces DIFFERENT content, which is always a
genuine defect (a non-deterministic materializer, or corrupted inputs),
never a legitimate rule change. Migration cost was zero: this table has
never held a production row (confirmed: no market_timeseries `.db` file
has ever existed on disk, and this package has zero production
importers as of Phase 17F.0's audit) -- the key changed before the table
was ever populated.

`as_of` IS MANDATORY on every multi-row read (`recent`/`range`) -- not
optional-with-a-default. This was designed once (16A) and left
unimplemented until now: omitting it is a call-signature error, so
look-ahead becomes impossible to write rather than something a reviewer
must catch. A candle's own data is only knowable once its window has
CLOSED, so the bound compares against `window_end`, not `window_start`.

`calc_version` IS ALSO MANDATORY on `recent`/`range`, for the same
reason and by the same operator decision: a result set silently mixing
two calculation versions would be a splice of two incompatible
definitions that looks like one continuous series -- invisible on a
chart, and exactly the failure this store's whole PK change exists to
prevent. `get_candle` (an exact single-row lookup) keeps `calc_version`
defaulted to `""` -- requesting one specific key is not "filtering
across versions," so the same rule does not apply there.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Iterable, List, Optional, Union

from .models import Candle

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    instrument              TEXT    NOT NULL,
    interval                TEXT    NOT NULL,
    window_start            TEXT    NOT NULL,
    window_end              TEXT    NOT NULL,
    calc_version            TEXT    NOT NULL DEFAULT '',
    kind                    TEXT    NOT NULL,
    open                    REAL    NOT NULL,
    high                    REAL    NOT NULL,
    low                     REAL    NOT NULL,
    close                   REAL    NOT NULL,
    volume                  REAL,
    tick_count              INTEGER NOT NULL,
    open_interest           REAL,
    schema_version          TEXT    NOT NULL,
    source_observation_ids  TEXT    NOT NULL DEFAULT '[]',
    materializer_id         TEXT    NOT NULL DEFAULT '',
    first_event_time        TEXT,
    last_event_time         TEXT,
    knowledge_boundary      TEXT,
    capture_event_overlap   TEXT    NOT NULL DEFAULT '[]',
    transformation_history  TEXT    NOT NULL DEFAULT '[]',
    PRIMARY KEY (instrument, interval, window_start, calc_version)
);
CREATE INDEX IF NOT EXISTS idx_candles_range
    ON candles (instrument, interval, calc_version, window_start DESC);
CREATE INDEX IF NOT EXISTS idx_candles_kind_time
    ON candles (kind, window_start);
"""


class ConflictingCandleError(Exception):
    """Raised when a DIFFERENT candle already exists for the same
    (instrument, interval, window_start, calc_version). Never silently
    overwritten -- a contradicting observation of settled history is a
    real problem the caller must see, not a value to quietly replace.

    Because `calc_version` is now part of the key, this can only mean
    the SAME calculation, over the SAME window, produced different
    content -- a non-deterministic materializer or corrupted inputs.
    Two different calc_versions disagreeing is expected and no longer
    raises this at all."""


class CandleStore:
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

    def __enter__(self) -> "CandleStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _run(self, fn, attempts: int = 5, base_delay: float = 0.01):
        """Retry on a transient `database is locked` -- same helper
        shape as mil_next/snapshot_journal.py's own `_run`."""
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
    def write_candle(self, candle: Candle) -> bool:
        """Returns True if newly inserted, False if an IDENTICAL candle
        already existed (idempotent retry). Raises
        `ConflictingCandleError` if a DIFFERENT candle exists for the
        same (instrument, interval, window_start, calc_version) key."""
        existing = self.get_candle(
            candle.instrument, candle.interval, candle.window_start, candle.calc_version
        )
        if existing is not None:
            if existing.to_dict() == candle.to_dict():
                return False
            raise ConflictingCandleError(
                f"candle already exists for {candle.instrument} {candle.interval} "
                f"{candle.window_start} calc_version={candle.calc_version!r} with "
                f"different content; history is immutable"
            )

        def _txn():
            with self._conn:
                self._conn.execute(
                    "INSERT INTO candles (instrument, interval, window_start, window_end, "
                    "calc_version, kind, open, high, low, close, volume, tick_count, "
                    "open_interest, schema_version, source_observation_ids, materializer_id, "
                    "first_event_time, last_event_time, knowledge_boundary, capture_event_overlap, "
                    "transformation_history) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (candle.instrument, candle.interval, candle.window_start, candle.window_end,
                     candle.calc_version, candle.kind, candle.open, candle.high, candle.low,
                     candle.close, candle.volume, candle.tick_count, candle.open_interest,
                     candle.schema_version, json.dumps(list(candle.source_observation_ids)),
                     candle.materializer_id, candle.first_event_time, candle.last_event_time,
                     candle.knowledge_boundary, json.dumps(list(candle.capture_event_overlap)),
                     json.dumps(list(candle.transformation_history))),
                )
            return True

        return self._run(_txn)

    def write_many(self, candles: Iterable[Candle]) -> int:
        return sum(1 for c in candles if self.write_candle(c))

    # -- Read ------------------------------------------------------------ #
    @staticmethod
    def _row_to_candle(row: sqlite3.Row) -> Candle:
        return Candle(
            instrument=row["instrument"], kind=row["kind"], interval=row["interval"],
            window_start=row["window_start"], window_end=row["window_end"],
            open=row["open"], high=row["high"], low=row["low"], close=row["close"],
            volume=row["volume"], tick_count=row["tick_count"],
            open_interest=row["open_interest"], schema_version=row["schema_version"],
            source_observation_ids=tuple(json.loads(row["source_observation_ids"] or "[]")),
            materializer_id=row["materializer_id"] or "",
            calc_version=row["calc_version"] or "",
            first_event_time=row["first_event_time"],
            last_event_time=row["last_event_time"],
            knowledge_boundary=row["knowledge_boundary"],
            capture_event_overlap=tuple(json.loads(row["capture_event_overlap"] or "[]")),
            transformation_history=tuple(json.loads(row["transformation_history"] or "[]")),
        )

    def get_candle(
        self, instrument: str, interval: str, window_start: str, calc_version: str = ""
    ) -> Optional[Candle]:
        """Exact single-row lookup by the full primary key. `calc_version`
        defaults to `""` (the provenance-less/legacy value) -- this is an
        exact key component, not a version filter, so the "never mix
        versions" rule below does not apply to this method."""
        row = self._conn.execute(
            "SELECT * FROM candles WHERE instrument=? AND interval=? AND window_start=? "
            "AND calc_version=?",
            (instrument, interval, window_start, calc_version),
        ).fetchone()
        return self._row_to_candle(row) if row is not None else None

    def recent(
        self, instrument: str, interval: str, count: int, *, as_of: str, calc_version: str
    ) -> List[Candle]:
        """The most recent `count` CLOSED candles, oldest-first (the
        order every technical indicator expects). Returns fewer than
        `count` when history is genuinely shorter -- never pads.

        `as_of` (REQUIRED): only candles whose `window_end <= as_of` are
        visible -- a candle's data is not knowable until its window has
        closed. This is the no-lookahead guarantee applied at the read
        boundary; omitting it is a call-signature error, not a silent
        "give me everything" default.

        `calc_version` (REQUIRED): a single result set may never mix
        calculation versions -- see the module docstring.
        """
        rows = self._conn.execute(
            "SELECT * FROM candles WHERE instrument=? AND interval=? AND calc_version=? "
            "AND window_end <= ? ORDER BY window_start DESC LIMIT ?",
            (instrument, interval, calc_version, as_of, count),
        ).fetchall()
        return [self._row_to_candle(r) for r in reversed(rows)]

    def range(
        self, instrument: str, interval: str, start: str, end: str, *, as_of: str, calc_version: str
    ) -> List[Candle]:
        """Closed candles with `start <= window_start < end`, oldest-first.
        Gaps are simply absent rows -- never forward-filled.

        `as_of` and `calc_version` are REQUIRED -- see `recent()`'s
        docstring; the same no-lookahead and no-version-mixing rules
        apply identically here.
        """
        rows = self._conn.execute(
            "SELECT * FROM candles WHERE instrument=? AND interval=? AND calc_version=? "
            "AND window_start >= ? AND window_start < ? AND window_end <= ? "
            "ORDER BY window_start ASC",
            (instrument, interval, calc_version, start, end, as_of),
        ).fetchall()
        return [self._row_to_candle(r) for r in rows]

    def calc_versions(self, instrument: str, interval: str) -> List[str]:
        """Every calc_version that has ever written a candle for this
        (instrument, interval) -- the practical way a caller discovers
        what to pass to `recent()`/`range()` without guessing."""
        rows = self._conn.execute(
            "SELECT DISTINCT calc_version FROM candles WHERE instrument=? AND interval=? "
            "ORDER BY calc_version",
            (instrument, interval),
        ).fetchall()
        return [r["calc_version"] for r in rows]

    def instruments(self, kind: Optional[str] = None) -> List[str]:
        if kind is None:
            rows = self._conn.execute(
                "SELECT DISTINCT instrument FROM candles ORDER BY instrument"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT DISTINCT instrument FROM candles WHERE kind=? ORDER BY instrument", (kind,)
            ).fetchall()
        return [r["instrument"] for r in rows]

    def count(self, instrument: Optional[str] = None) -> int:
        if instrument is None:
            return self._conn.execute("SELECT COUNT(*) AS n FROM candles").fetchone()["n"]
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM candles WHERE instrument=?", (instrument,)
        ).fetchone()["n"]
