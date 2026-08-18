"""Market Reality Timeline — Phase 17H.6.

REALITY-ONLY, per explicit decision on 2026-08-13: this phase builds a
query/filter layer over already-stored `MarketRealitySnapshot` rows,
computing NOTHING new. Every filter below compares against a value that
was already captured and stored by Phase 17H.4/17H.5 -- no returns, no
moving averages, no percentiles, no trend/regime classification, no
similarity scoring.

This boundary was drawn deliberately, not by default: the fuller
request (feature extraction, historical similarity, market memory
statistics) crosses into Understanding/Intelligence-tier territory that
`docs/PHASE_17H2_HISTORICAL_REALITY_SCHEMA.md` explicitly excludes from
Reality ("No: RSI, EMA, MACD, Supertrend... Reality must remain
immutable") -- a rule enforced consistently across this entire
engagement. That work remains a real, named, future decision, not
silently declined and not silently folded in here.

"Give me all days where VIX was between 10 and 13" is answerable today
because `vix.close` is a real, already-stored fact. "Give me all days
where the market was trending" is not answerable here, because
"trending" does not exist as a captured fact -- it would have to be
computed, and this phase computes nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .models import MarketRealitySnapshot
from .store import MarketRealitySnapshotStore


@dataclass(frozen=True)
class RealityQuery:
    """Every field is a range/equality filter over an ALREADY-STORED
    value. `None` means "no constraint on this field" -- never "zero"
    or "missing". A day whose relevant instrument is `None` (real
    absence, per Phase 17H.5's no-fake-completeness rule) never matches
    a numeric filter on that instrument -- absence is not a value in
    range, it is the absence of one."""

    date_from: Optional[str] = None
    date_to: Optional[str] = None
    completeness: Optional[str] = None
    is_final: Optional[bool] = None

    spot_close_min: Optional[float] = None
    spot_close_max: Optional[float] = None
    spot_open_min: Optional[float] = None
    spot_open_max: Optional[float] = None
    spot_high_min: Optional[float] = None
    spot_high_max: Optional[float] = None
    spot_low_min: Optional[float] = None
    spot_low_max: Optional[float] = None

    futures_close_min: Optional[float] = None
    futures_close_max: Optional[float] = None

    vix_close_min: Optional[float] = None
    vix_close_max: Optional[float] = None


def _in_range(value: Optional[float], lo: Optional[float], hi: Optional[float]) -> bool:
    if value is None:
        return False  # Absence never satisfies a numeric constraint.
    if lo is not None and value < lo:
        return False
    if hi is not None and value > hi:
        return False
    return True


def _matches(snap: MarketRealitySnapshot, q: RealityQuery) -> bool:
    if q.completeness is not None and snap.completeness != q.completeness:
        return False
    if q.is_final is not None and snap.is_final != q.is_final:
        return False

    spot = snap.spot
    if any(x is not None for x in (q.spot_close_min, q.spot_close_max)):
        if not _in_range(spot.close if spot else None, q.spot_close_min, q.spot_close_max):
            return False
    if any(x is not None for x in (q.spot_open_min, q.spot_open_max)):
        if not _in_range(spot.open if spot else None, q.spot_open_min, q.spot_open_max):
            return False
    if any(x is not None for x in (q.spot_high_min, q.spot_high_max)):
        if not _in_range(spot.high if spot else None, q.spot_high_min, q.spot_high_max):
            return False
    if any(x is not None for x in (q.spot_low_min, q.spot_low_max)):
        if not _in_range(spot.low if spot else None, q.spot_low_min, q.spot_low_max):
            return False

    futures = snap.futures
    if any(x is not None for x in (q.futures_close_min, q.futures_close_max)):
        if not _in_range(futures.close if futures else None, q.futures_close_min, q.futures_close_max):
            return False

    vix = snap.vix
    if any(x is not None for x in (q.vix_close_min, q.vix_close_max)):
        if not _in_range(vix.close if vix else None, q.vix_close_min, q.vix_close_max):
            return False

    return True


class MarketRealityTimeline:
    """A queryable time-series over already-stored `MarketRealitySnapshot`
    rows. Read-only -- writes remain `MarketRealitySnapshotStore`'s job,
    same separation of concerns as every other store/query pair in this
    project."""

    def __init__(self, store: MarketRealitySnapshotStore) -> None:
        self._store = store

    def get(self, date: str) -> Optional[MarketRealitySnapshot]:
        return self._store.get(date)

    def range(self, date_from: str, date_to: str) -> List[MarketRealitySnapshot]:
        """Every stored snapshot in [date_from, date_to], ordered by
        date -- no filtering, the raw timeline."""
        return self._store.range(date_from, date_to)

    def query(self, q: RealityQuery) -> List[MarketRealitySnapshot]:
        """Filter stored snapshots against already-captured fields only.
        `q.date_from`/`q.date_to` default to the full available range if
        unset (a wide but bounded SQL scan, never an unbounded one)."""
        date_from = q.date_from or "0000-01-01"
        date_to = q.date_to or "9999-12-31"
        candidates = self._store.range(date_from, date_to)
        return [snap for snap in candidates if _matches(snap, q)]
