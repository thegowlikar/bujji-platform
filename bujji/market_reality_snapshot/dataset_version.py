"""DatasetVersion — Phase 18.7.

Assembles a date-range's worth of already-proven, already-immutable
Reality facts into ONE identified, fingerprinted artifact. Read-only:
never writes to `HistoricalObservationStore`, never creates a new
store of its own, never touches raw Reality. Reuses, does not
duplicate:

  * `MarketRealitySnapshot.fingerprint()` (Phase 18.3) -- one
    fingerprint per covered date, folded into `fingerprint_lineage`.
  * `MarketRealitySnapshot.reconstruction_version` (Phase 18.3) -- the
    dataset's own `reconstruction_version` is REJECTED (raises
    ValueError) if the covered dates were not all built under the same
    version; a dataset spanning a reconstruction-logic change is a
    real inconsistency this module refuses to silently paper over.
  * `HistoricalLineage.ingestion_run_id` (Phase 17H.2/17I.10), read
    directly off each real stored row for every date/instrument in
    range -- not `HistoricalObservationStore.ingestion_runs_for()`,
    because that method is keyed by an "instrument" label the 6
    daily/intraday backfill scripts populate but the options capture
    script never calls `record_ingestion_run()` at all (verified this
    phase by grep -- a real, disclosed asymmetry). Reading
    `lineage.ingestion_run_id` off the rows themselves works
    identically for every instrument, options included, and needed no
    new store method.
  * `replay_engine.fingerprint_state()` (Phase 18.3's own precedent) --
    the `dataset_version_id` itself is computed the same way a
    snapshot's own fingerprint is, no second hashing mechanism.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Optional, Tuple

from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_calendar import MarketCalendar
from bujji.replay_engine.engine import fingerprint_state

from .builder import (
    FUTURES_CONTINUOUS_IDENTITY,
    SPOT_SYMBOL,
    VIX_SYMBOL,
    _day_bounds,
)
from .models import RESOLUTION_DAILY
from .research_calendar import STATUS_READY, build_research_calendar


@dataclass(frozen=True)
class DatasetVersion:
    dataset_version_id: str
    created_at: str
    coverage_start: str
    coverage_end: str
    resolution: str
    included_components: Tuple[str, ...]      # which of spot/futures/vix/options had ANY data in range.
    reconstruction_version: str
    ingestion_run_references: Tuple[str, ...]  # sorted, de-duplicated real ingestion_run_id values.
    fingerprint_lineage: Tuple[str, ...]       # one MarketRealitySnapshot.fingerprint() per covered date, in date order.
    ready_dates: Tuple[str, ...]               # dates whose ResearchCalendar status was READY.
    incomplete_dates: Tuple[str, ...]          # every other date in range, honestly listed, never dropped silently.

    def to_dict(self) -> dict:
        return {
            "dataset_version_id": self.dataset_version_id, "created_at": self.created_at,
            "coverage_start": self.coverage_start, "coverage_end": self.coverage_end,
            "resolution": self.resolution, "included_components": list(self.included_components),
            "reconstruction_version": self.reconstruction_version,
            "ingestion_run_references": list(self.ingestion_run_references),
            "fingerprint_lineage": list(self.fingerprint_lineage),
            "ready_dates": list(self.ready_dates), "incomplete_dates": list(self.incomplete_dates),
        }


def _ingestion_run_ids(historical_store, identity_or_prefix, resolution, day_start, day_end, by_prefix):
    fn = historical_store.range_by_prefix if by_prefix else historical_store.range
    rows = fn(identity_or_prefix, resolution, day_start, day_end)
    return {r.lineage.ingestion_run_id for r in rows}


def build_dataset_version(
    start_date: str,
    end_date: str,
    *,
    historical_store: HistoricalObservationStore,
    resolution: str = RESOLUTION_DAILY,
    as_of_time_of_day: Optional[str] = None,
    calendar: Optional[MarketCalendar] = None,
    now: Optional[datetime.datetime] = None,
) -> DatasetVersion:
    """Never fabricates coverage: `ready_dates`/`incomplete_dates`
    together always equal the FULL requested date range -- a date this
    module could not fully verify is listed as incomplete, never
    silently omitted (PHASE_18_4's own "no fake completeness" rule,
    applied here to a multi-date artifact for the first time)."""
    IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    calendar_entries = build_research_calendar(
        start_date, end_date, historical_store=historical_store,
        resolution=resolution, as_of_time_of_day=as_of_time_of_day, calendar=calendar,
    )

    reconstruction_versions = {e.readiness.reconstruction_version for e in calendar_entries}
    if len(reconstruction_versions) > 1:
        raise ValueError(
            f"dates in [{start_date}, {end_date}] were built under different "
            f"reconstruction_version values ({sorted(reconstruction_versions)}) -- "
            "a DatasetVersion must not silently mix reconstruction logic. Rebuild "
            "the older dates, or split this into two DatasetVersions."
        )
    reconstruction_version = next(iter(reconstruction_versions)) if reconstruction_versions else "unknown"

    ready_dates = tuple(e.date for e in calendar_entries if e.status == STATUS_READY)
    incomplete_dates = tuple(e.date for e in calendar_entries if e.status != STATUS_READY)
    fingerprint_lineage = tuple(e.readiness.fingerprint for e in calendar_entries)

    included_components = tuple(sorted({
        name
        for e in calendar_entries
        for name, present in (
            ("spot", e.readiness.spot_available), ("futures", e.readiness.futures_available),
            ("vix", e.readiness.vix_available), ("options", e.readiness.options_available),
        )
        if present
    }))

    # PHASE_18_10: options run ids are read off `e.readiness.options_ingestion_run_ids`
    # (populated for free while `_build_options_snapshot` already
    # walked these exact rows to build the calendar entry itself) --
    # NOT re-queried here. This removes the second, identical
    # `range_by_prefix()` call PHASE_18_9's own profiling confirmed was
    # pure duplicate work. Spot/futures/vix remain three small,
    # already-fast, exact-match `range()` lookups (PHASE_18_9's own
    # profile: 0.017s total across 30 such calls) -- left unchanged,
    # per this phase's own "do not optimize prematurely beyond the
    # identified bottleneck" instruction.
    run_ids = set()
    for e in calendar_entries:
        day_start, day_end = _day_bounds(e.date)
        run_ids |= _ingestion_run_ids(historical_store, SPOT_SYMBOL, resolution, day_start, day_end, False)
        run_ids |= _ingestion_run_ids(historical_store, FUTURES_CONTINUOUS_IDENTITY, resolution, day_start, day_end, False)
        run_ids |= _ingestion_run_ids(historical_store, VIX_SYMBOL, resolution, day_start, day_end, False)
        run_ids |= set(e.readiness.options_ingestion_run_ids)
    ingestion_run_references = tuple(sorted(run_ids))

    created_at = (now or datetime.datetime.now(IST)).isoformat()

    identity_payload = {
        "coverage_start": start_date, "coverage_end": end_date, "resolution": resolution,
        "included_components": included_components, "reconstruction_version": reconstruction_version,
        "fingerprint_lineage": fingerprint_lineage,
    }
    dataset_version_id = fingerprint_state(identity_payload)

    return DatasetVersion(
        dataset_version_id=dataset_version_id, created_at=created_at,
        coverage_start=start_date, coverage_end=end_date, resolution=resolution,
        included_components=included_components, reconstruction_version=reconstruction_version,
        ingestion_run_references=ingestion_run_references, fingerprint_lineage=fingerprint_lineage,
        ready_dates=ready_dates, incomplete_dates=incomplete_dates,
    )
