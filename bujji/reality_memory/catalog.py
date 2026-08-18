"""Reality Memory Catalog — Phase 17J.1.

Read-only. Composes `RealityMemoryEvent` projections. Holds no state,
writes nothing, computes nothing about the market -- every value
returned traces to a field already present on the underlying Reality
stores.

NAMED `RealityMemoryCatalog`, not `RealityMemoryIndex` (confusable with
`RealityCoverageIndex`, 17I.4 -- availability manifest, different
concept) and not `RealityMemoryTimeline` (confusable with
`MarketRealityTimeline`, 17H.6 -- a condition-filter query, different
concept).

REAL, LIVE-VERIFIED FINDING THIS DESIGN CORRECTS (2026-08-14, this
phase): the persisted `MarketRealitySnapshotStore`
(`data/historical_reality/normalized/market_reality_snapshots.db`) is
STALE -- built before Phase 17H.6/17H.9 added historical futures/VIX/
intraday ingestion, it covers only 2020-01-01..2020-12-31 (252 rows)
and disagrees on `completeness` with a fresh reconstruction for 20/20
sampled dates (checked live, not assumed). Reading from it here would
have silently handed Memory a materially incomplete substrate despite
Historical Reality itself having full 1998/2008/2018->today coverage.

CORRECTED DESIGN: build every `RealityMemoryEvent` via
`build_market_reality_snapshot()` (Phase 17H.5/17H.7) against the live
`HistoricalObservationStore`/`RawObservationStore` directly, on every
call -- never the persisted, possibly-stale snapshot store. This is the
exact same function `MarketRealitySnapshotStore`'s own writers use to
build a row in the first place (and the same one
`reconstruct_market_reality()` calls internally before flattening its
own output) -- this catalog simply never persists or trusts a cached
copy of that computation, and keeps the full, unflattened
`MarketRealitySnapshot` shape (volume, expiry_date, complete lineage)
rather than `reconstruct_market_reality()`'s coarser dict view. Still no
new store: `build_market_reality_snapshot()` reads only already-existing
stores.

Known cost, stated rather than hidden: each `get()`/`range()` call
recomputes from source rather than reading an index, so a `range()`
over a very wide window issues real per-instrument SQL queries per
date. Acceptable for this phase's scope (correctness over performance,
matching 17H.7's own "recompute rather than trust a stale cache"
precedent); a materialized, kept-fresh index is a legitimate future
optimization, not attempted here.
"""
from __future__ import annotations

import datetime
from typing import List, Optional

from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_reality.store import RawObservationStore
from bujji.market_reality_snapshot.builder import build_market_reality_snapshot

from .models import RealityMemoryEvent


class RealityMemoryCatalog:
    """Read-only projection layer. Similarity/comparison logic is
    explicitly OUT of scope here (Phase 17J.0 §5/§6) -- this class only
    retrieves and projects, never matches or scores."""

    def __init__(self, *, historical_store: HistoricalObservationStore,
                 live_store: Optional[RawObservationStore] = None) -> None:
        self._historical_store = historical_store
        self._live_store = live_store

    def get(self, date: str, *, now: Optional[datetime.datetime] = None) -> RealityMemoryEvent:
        """Always returns an event -- never `None` -- because a date
        with zero Reality behind it is still a real, honest fact
        (`completeness=EMPTY`), not an absence of the record itself.
        Matches `build_market_reality_snapshot()`'s own contract."""
        snap = build_market_reality_snapshot(
            date, historical_store=self._historical_store,
            live_store=self._live_store, now=now,
        )
        return RealityMemoryEvent.from_snapshot(snap)

    def range(self, date_from: str, date_to: str, *,
               now: Optional[datetime.datetime] = None) -> List[RealityMemoryEvent]:
        """One `RealityMemoryEvent` per calendar date in
        `[date_from, date_to]`, inclusive. Explicit bounds required --
        deliberately no "default to everything" convenience, since
        every date costs a real recomputation (see module docstring)."""
        start = datetime.date.fromisoformat(date_from)
        end = datetime.date.fromisoformat(date_to)
        if end < start:
            return []
        events = []
        cursor = start
        one_day = datetime.timedelta(days=1)
        while cursor <= end:
            events.append(self.get(cursor.isoformat(), now=now))
            cursor += one_day
        return events
