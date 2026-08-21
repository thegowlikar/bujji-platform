"""bujji.market_microstructure.store — Phase 19.20.3. SQLite, WAL mode.

Follows the exact same reliability discipline as
`bujji.historical_reality.store.HistoricalObservationStore` (itself
following `market_timeseries.store`/`journal.py`): same WAL journal
mode + `synchronous=FULL`, same retry-on-locked helper shape, same
natural-key idempotency contract (identical row re-written -> no-op;
a DIFFERENT payload under the same natural key -> raised, never
silently overwritten).

ISOLATION (Phase 19.20.3's own explicit requirement): this is a
SEPARATE file, opened with its own `sqlite3.connect()` call, own
schema, own class. It never imports, constructs, or opens
`bujji.historical_reality.store.HistoricalObservationStore` or
`historical_observations.db` — proven by
`tests/test_market_microstructure/test_store.py`'s own isolation test,
not merely asserted here.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import List, Optional, Union

from .models import MinuteObservation

_SCHEMA = """
CREATE TABLE IF NOT EXISTS minute_observations (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument                  TEXT    NOT NULL,
    kind                        TEXT    NOT NULL,
    session_date                TEXT    NOT NULL,
    window_start                TEXT    NOT NULL,
    window_end                  TEXT    NOT NULL,
    open                        REAL    NOT NULL,
    high                        REAL    NOT NULL,
    low                         REAL    NOT NULL,
    close                       REAL    NOT NULL,
    tick_count                  INTEGER NOT NULL,
    max_tick_silence_seconds    REAL,
    avg_tick_interval_seconds   REAL,
    max_price_move              REAL    NOT NULL,
    max_premium_move            REAL,
    open_interest                REAL,
    strike                       REAL,
    option_type                  TEXT,
    first_tick_timestamp         TEXT    NOT NULL,
    last_tick_timestamp          TEXT    NOT NULL,
    rejected_tick_count          INTEGER NOT NULL DEFAULT 0,
    observation_quality_score    REAL    NOT NULL,
    schema_version                TEXT    NOT NULL,
    source                       TEXT    NOT NULL,
    natural_key                  TEXT    NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_minute_obs_natural_key
    ON minute_observations (natural_key);
CREATE INDEX IF NOT EXISTS idx_minute_obs_session_date
    ON minute_observations (session_date, instrument);
CREATE INDEX IF NOT EXISTS idx_minute_obs_instrument
    ON minute_observations (instrument, window_start);

CREATE TABLE IF NOT EXISTS capture_session_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date        TEXT    NOT NULL,
    started_at          TEXT    NOT NULL,
    stopped_at          TEXT,
    connect_count       INTEGER,
    disconnect_events   INTEGER,
    last_error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_capture_session_log_date
    ON capture_session_log (session_date);
"""


def _natural_key(instrument: str, window_start: str) -> str:
    return "|".join((instrument, window_start))


class ConflictingMinuteObservationError(Exception):
    """A DIFFERENT observation already exists for the same
    (instrument, window_start) natural key. Never silently overwritten
    — matches HistoricalObservationStore's own conflict discipline."""


class MicrostructureIntegrityError(Exception):
    """Raised at open time when `PRAGMA integrity_check` does not
    report 'ok' — fail loud, never silently continue against a
    corrupted database file."""


class MicrostructureStore:
    """One SQLite file, isolated from every Phase 19.19 store. Safe to
    construct repeatedly against the same path, including across a
    process restart — never truncates, never drops a table."""

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

        status = self.integrity_check()
        if status != "ok":
            raise MicrostructureIntegrityError(
                f"PRAGMA integrity_check reported {status!r} for {self._path!r} — refusing to "
                f"proceed against a database that may not be trustworthy."
            )

    @property
    def path(self) -> str:
        return self._path

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "MicrostructureStore":
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

    # -- Durability -------------------------------------------------------- #
    def journal_mode(self) -> str:
        return self._conn.execute("PRAGMA journal_mode").fetchone()[0]

    def integrity_check(self) -> str:
        return self._conn.execute("PRAGMA integrity_check").fetchone()[0]

    # -- Write --------------------------------------------------------------- #
    def write(self, obs: MinuteObservation) -> bool:
        """Returns True if a new row was inserted, False if an
        identical row already existed (idempotent no-op). Raises
        `ConflictingMinuteObservationError` if a DIFFERENT observation
        already occupies this natural key — never silently overwritten."""
        nkey = _natural_key(obs.instrument, obs.window_start)
        d = obs.to_dict()

        def _txn():
            cur = self._conn.execute(
                "SELECT open, high, low, close, tick_count "
                "FROM minute_observations WHERE natural_key = ?",
                (nkey,),
            )
            row = cur.fetchone()
            if row is not None:
                existing = (row["open"], row["high"], row["low"], row["close"], row["tick_count"])
                incoming = (obs.open, obs.high, obs.low, obs.close, obs.tick_count)
                if existing == incoming:
                    return False  # Idempotent no-op — identical fact re-written.
                raise ConflictingMinuteObservationError(
                    f"natural_key={nkey!r} already holds a different observation "
                    f"(existing OHLC/tick_count={existing}, incoming={incoming}) — "
                    "a re-observation with different content must be reviewed explicitly, "
                    "never silently overwritten."
                )
            self._conn.execute(
                "INSERT INTO minute_observations ("
                " instrument, kind, session_date, window_start, window_end,"
                " open, high, low, close, tick_count,"
                " max_tick_silence_seconds, avg_tick_interval_seconds, max_price_move,"
                " max_premium_move, open_interest, strike, option_type,"
                " first_tick_timestamp, last_tick_timestamp, rejected_tick_count,"
                " observation_quality_score, schema_version, source, natural_key"
                ") VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?, ?,?,?,?, ?,?,?, ?,?,?,?)",
                (
                    d["instrument"], d["kind"], d["session_date"], d["window_start"], d["window_end"],
                    d["open"], d["high"], d["low"], d["close"], d["tick_count"],
                    d["max_tick_silence_seconds"], d["avg_tick_interval_seconds"], d["max_price_move"],
                    d["max_premium_move"], d["open_interest"], d["strike"], d["option_type"],
                    d["first_tick_timestamp"], d["last_tick_timestamp"], d["rejected_tick_count"],
                    d["observation_quality_score"], d["schema_version"], "market_microstructure", nkey,
                ),
            )
            self._conn.commit()
            return True

        return self._run(_txn)

    def write_many(self, observations) -> List[bool]:
        return [self.write(o) for o in observations]

    def record_capture_session(
        self, *, session_date: str, started_at: str, stopped_at: Optional[str] = None,
        connect_count: Optional[int] = None, disconnect_events: Optional[int] = None,
        last_error: Optional[str] = None,
    ) -> int:
        """Append-only — one row per real session attempt (mirrors
        HistoricalObservationStore's own `ingestion_runs` convention).
        Returns the new row's id."""
        def _txn():
            cur = self._conn.execute(
                "INSERT INTO capture_session_log "
                "(session_date, started_at, stopped_at, connect_count, disconnect_events, last_error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_date, started_at, stopped_at, connect_count, disconnect_events, last_error),
            )
            self._conn.commit()
            return cur.lastrowid
        return self._run(_txn)

    # -- Read ------------------------------------------------------------ #
    @staticmethod
    def _row_to_observation(row: sqlite3.Row) -> MinuteObservation:
        return MinuteObservation(
            instrument=row["instrument"], kind=row["kind"], session_date=row["session_date"],
            window_start=row["window_start"], window_end=row["window_end"],
            open=row["open"], high=row["high"], low=row["low"], close=row["close"],
            tick_count=row["tick_count"],
            max_tick_silence_seconds=row["max_tick_silence_seconds"],
            avg_tick_interval_seconds=row["avg_tick_interval_seconds"],
            max_price_move=row["max_price_move"],
            max_premium_move=row["max_premium_move"],
            open_interest=row["open_interest"],
            strike=row["strike"],
            option_type=row["option_type"],
            first_tick_timestamp=row["first_tick_timestamp"],
            last_tick_timestamp=row["last_tick_timestamp"],
            rejected_tick_count=row["rejected_tick_count"],
            observation_quality_score=row["observation_quality_score"],
            schema_version=row["schema_version"],
        )

    def get_by_date(self, session_date: str) -> List[MinuteObservation]:
        """Deterministic ordering: instrument, then window_start —
        never insertion order. Empty result for an unknown date."""
        cur = self._conn.execute(
            "SELECT * FROM minute_observations WHERE session_date = ? "
            "ORDER BY instrument ASC, window_start ASC",
            (session_date,),
        )
        return [self._row_to_observation(r) for r in cur.fetchall()]

    def get_by_instrument(self, instrument: str) -> List[MinuteObservation]:
        """Deterministic ordering: window_start ascending."""
        cur = self._conn.execute(
            "SELECT * FROM minute_observations WHERE instrument = ? ORDER BY window_start ASC",
            (instrument,),
        )
        return [self._row_to_observation(r) for r in cur.fetchall()]

    def count(self, instrument: Optional[str] = None) -> int:
        if instrument is None:
            cur = self._conn.execute("SELECT COUNT(*) AS n FROM minute_observations")
        else:
            cur = self._conn.execute(
                "SELECT COUNT(*) AS n FROM minute_observations WHERE instrument = ?", (instrument,),
            )
        return cur.fetchone()["n"]

    def get_capture_sessions(self, session_date: str) -> List[dict]:
        """Read-only accessor for `capture_session_log` rows on a given
        date -- added Phase 19.20.4, for the integrity/reconciliation
        layer's feed-interruption analysis. Deterministic ordering:
        started_at ascending."""
        cur = self._conn.execute(
            "SELECT session_date, started_at, stopped_at, connect_count, disconnect_events, last_error "
            "FROM capture_session_log WHERE session_date = ? ORDER BY started_at ASC",
            (session_date,),
        )
        return [dict(r) for r in cur.fetchall()]
