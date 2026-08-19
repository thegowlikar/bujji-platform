"""The daily refresh: build structure once, from completed bars.

WHY A SNAPSHOT AND NOT A PER-CYCLE COMPUTATION. Detection over the last 1,500
five-minute bars scans them at three strengths and three impulse multiples.
Doing that inside every five-minute decision cycle would burn the session's
time budget re-deriving something that cannot have changed: the bars it reads
are yesterday's and they are finished. So it runs ONCE, after the close, over
bars that are complete -- and the session reads the result.

FRESHNESS IS PUBLISHED, NOT ASSUMED. Every snapshot carries the date it was
built and the instant it was cut at. A reader can always ask "how old is this
map" and get a real answer, which is the difference between using yesterday's
structure knowingly and using it by accident.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Optional

from . import taxonomy
from .engine import detect_levels, detect_zones
from .models import LevelSet, ZoneSet
from .store_reader import DEFAULT_DB_PATH, BarLoadResult, load_bars

DEFAULT_SNAPSHOT_DIR = "/opt/bujji/app/data/price_levels"

# The tail of history that matters. Levels are about REACHABLE structure, so
# the recent past is what counts -- and this is a cost control, not a decay
# rule: the proximity band in L-3 is what decides relevance. 1,500 five-minute
# bars is roughly twenty sessions.
DEFAULT_BAR_LIMIT = 1500


@dataclass(frozen=True)
class LevelsSnapshot:
    """One day's map of structure, and the evidence of how it was built."""

    built_for: str                 # the trading date this snapshot serves
    built_at: str                  # when it was actually built
    as_of: Optional[str]           # the no-lookahead cut, if any
    instrument: str
    resolution: str
    levels: LevelSet
    zones: ZoneSet
    load: Dict[str, Any]           # BarLoadResult.to_dict() -- including rows skipped
    schema_version: str = taxonomy.SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "built_for": self.built_for, "built_at": self.built_at, "as_of": self.as_of,
            "instrument": self.instrument, "resolution": self.resolution,
            "levels": self.levels.to_dict(), "zones": self.zones.to_dict(),
            "load": self.load, "schema_version": self.schema_version,
        }


def build_snapshot(
    *,
    built_for: str,
    built_at: str,
    instrument: str = "NSE:NIFTY50-INDEX",
    resolution: str = "FIVE_MINUTE",
    db_path: str = DEFAULT_DB_PATH,
    bar_limit: int = DEFAULT_BAR_LIMIT,
    as_of: Optional[str] = None,
) -> LevelsSnapshot:
    """Build today's map from real completed bars.

    Never raises on thin data: `detect_levels`/`detect_zones` return their own
    INSUFFICIENT_HISTORY status with a reason, and that is a perfectly good
    snapshot to publish -- it says, truthfully, that we could not see.
    """
    load: BarLoadResult = load_bars(
        db_path=db_path, instrument=instrument, resolution=resolution,
        limit=bar_limit, before=as_of,
    )
    levels = detect_levels(load.bars, source_resolution=resolution, as_of=as_of)
    zones = detect_zones(load.bars, source_resolution=resolution, as_of=as_of)
    return LevelsSnapshot(
        built_for=built_for, built_at=built_at, as_of=as_of, instrument=instrument,
        resolution=resolution, levels=levels, zones=zones, load=load.to_dict(),
    )


def snapshot_path(built_for: str, directory: str = DEFAULT_SNAPSHOT_DIR) -> str:
    return os.path.join(directory, f"levels_{built_for}.json")


def write_snapshot(snapshot: LevelsSnapshot, directory: str = DEFAULT_SNAPSHOT_DIR) -> str:
    """Write one dated snapshot. Dated on purpose: overwriting a single
    `latest.json` would destroy the record of what Bujji believed on the day
    it made a decision, which is exactly what an audit needs."""
    os.makedirs(directory, exist_ok=True)
    path = snapshot_path(snapshot.built_for, directory)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(snapshot.to_dict(), handle, indent=2, sort_keys=True)
    return path


def read_snapshot_raw(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def latest_snapshot_path(
    on_or_before: str, directory: str = DEFAULT_SNAPSHOT_DIR,
) -> Optional[str]:
    """The newest snapshot not built AFTER `on_or_before`.

    Deliberately not "the newest file": a session replaying an older date
    must not silently pick up a map built from bars that had not happened
    yet. Returns None when nothing qualifies -- the caller then has no map
    and must say so, rather than proceeding with an empty one.
    """
    if not os.path.isdir(directory):
        return None
    candidates = []
    for name in os.listdir(directory):
        if not (name.startswith("levels_") and name.endswith(".json")):
            continue
        stamp = name[len("levels_"):-len(".json")]
        if stamp <= on_or_before:
            candidates.append((stamp, os.path.join(directory, name)))
    if not candidates:
        return None
    return max(candidates)[1]


def snapshot_age_days(snapshot_for: str, today: str) -> int:
    """How stale the map is, in calendar days. Published so a reader can
    decide, rather than discovering staleness by its effects."""
    return (date.fromisoformat(today) - date.fromisoformat(snapshot_for)).days
