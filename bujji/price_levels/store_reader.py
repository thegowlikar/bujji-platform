"""Reading real bars out of the normalized store.

ONE JOB, AND ONE HONESTY RULE. The store now holds two shapes for the same
instrument: backfilled OHLC bars, and -- since CP-D.2 -- live 60-second POINT
SAMPLES carrying only `ltp`. A level needs a high and a low. Deriving them
from a single traded price would invent values never observed, so point-sample
rows are SKIPPED here and the number skipped is returned, never swallowed.

That count matters operationally: if it ever exceeds the bar count, the
caller is reading the wrong series and the levels built from it would be
thin without anything looking wrong.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .models import Bar

DEFAULT_DB_PATH = ("/opt/bujji/app/data/historical_reality/normalized/"
                   "historical_observations.db")


@dataclass(frozen=True)
class BarLoadResult:
    bars: Tuple[Bar, ...]
    rows_seen: int
    skipped_not_a_bar: int
    instrument: str
    resolution: str
    source: str

    @property
    def usable(self) -> bool:
        return bool(self.bars)

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument, "resolution": self.resolution,
            "source": self.source, "rows_seen": self.rows_seen,
            "bars": len(self.bars), "skipped_not_a_bar": self.skipped_not_a_bar,
        }


def load_bars(
    *,
    db_path: str = DEFAULT_DB_PATH,
    instrument: str,
    resolution: str,
    source: str = "fyers_historical",
    limit: Optional[int] = None,
    before: Optional[str] = None,
) -> BarLoadResult:
    """Load OHLC bars for one series, oldest first.

    `before` is the no-lookahead cut applied IN SQL, so bars from after the
    assessment instant are never even materialised. `limit` keeps the most
    RECENT n bars (levels are about reachable structure, so the tail is what
    matters) -- applied after the cut, never instead of it.
    """
    query = ("SELECT timestamp, payload FROM historical_observations "
             "WHERE instrument_identity = ? AND resolution = ? AND source = ?")
    params: List[object] = [instrument, resolution, source]
    if before is not None:
        query += " AND timestamp < ?"
        params.append(before)
    query += " ORDER BY timestamp"

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    if limit is not None and limit > 0:
        rows = rows[-limit:]

    bars: List[Bar] = []
    skipped = 0
    for timestamp, payload in rows:
        try:
            record = json.loads(payload)
        except (TypeError, ValueError):
            skipped += 1
            continue
        try:
            bars.append(Bar.from_mapping({**record, "timestamp": timestamp}))
        except ValueError:
            # A live point sample ({"ltp": ...}) lands here, by design.
            skipped += 1

    return BarLoadResult(
        bars=tuple(bars), rows_seen=len(rows), skipped_not_a_bar=skipped,
        instrument=instrument, resolution=resolution, source=source,
    )
