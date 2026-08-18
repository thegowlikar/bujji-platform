"""Research Session Readiness contract — Phase 18.5.

Answers ONE question, honestly: "given a date and resolution, which
Reality-tier components actually exist to build a market state from?"
Pure presence-checking over already-existing, immutable stores -- no
new storage, no derived indicators, no strategy logic, no backtest
concept. Reuses `market_reality_snapshot.builder.build_market_reality_snapshot()`
verbatim (the same reconstruction this project already trusts,
PHASE_18_1/18.3) rather than re-implementing presence checks against
`HistoricalObservationStore` directly -- one source of truth for "is
this instrument available," never two.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from bujji.historical_reality.store import HistoricalObservationStore

from .builder import build_market_reality_snapshot
from .models import RESOLUTION_DAILY


@dataclass(frozen=True)
class ResearchSessionReadiness:
    """A snapshot's own presence facts, restated as explicit yes/no
    answers -- nothing here is computed FROM market values (no
    indicator, no signal); every field is a presence check against
    already-reconstructed `MarketRealitySnapshot` components."""

    date: str
    resolution: str
    as_of_time: Optional[str]
    spot_available: bool
    futures_available: bool
    vix_available: bool
    options_available: bool
    # Depth (futures order-book) lives ONLY in Layer 0
    # (`RawObservationStore`) today -- `MarketRealitySnapshot` has never
    # integrated it (PHASE_18_0 §3's own finding, unchanged through
    # 18.1-18.4). `None` here means "not determined by this contract,"
    # deliberately distinct from `False` ("checked, absent") -- this
    # phase does not extend depth integration, so claiming a real
    # True/False here would overstate what was actually verified.
    depth_available: Optional[bool]
    # A coarse, SNAPSHOT-LEVEL signal: True iff at least one available
    # component carries a real `certification_ref`. Deliberately NOT a
    # per-instrument breakdown -- `MarketRealitySnapshot` only exposes
    # `certification_refs` as one de-duplicated, snapshot-wide tuple
    # (PHASE_17H.5's own model shape); a true per-instrument
    # certification-status check would require dereferencing each
    # `source_observation_ids` entry back through the store's own
    # `record` JSON blob, a real capability but a heavier one than this
    # minimal contract adds. Documented here, not silently implied.
    certified_lineage_available: bool
    completeness: str
    fingerprint: str
    reconstruction_version: str
    # PHASE_18_10, additive: the same real ingestion_run_id values
    # `OptionsSnapshot.ingestion_run_ids` already collected for free
    # while building the options chain -- surfaced here so
    # `DatasetVersion` (Phase 18.7) can read them off this readiness
    # result instead of re-querying `range_by_prefix()` a second time
    # for the identical date/prefix/resolution window (PHASE_18_9's own
    # confirmed duplicate-query finding). `()` when options are absent
    # or resolution never had any (e.g. DAILY).
    options_ingestion_run_ids: Tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        """spot + futures + vix + options all present -- the exact bar
        PHASE_18_4's own Iron Condor scenario needed and, before
        PHASE_18_5's backfill, no real date could clear."""
        return self.spot_available and self.futures_available and self.vix_available and self.options_available

    @property
    def missing(self) -> Tuple[str, ...]:
        names = []
        if not self.spot_available:
            names.append("spot")
        if not self.futures_available:
            names.append("futures")
        if not self.vix_available:
            names.append("vix")
        if not self.options_available:
            names.append("options")
        return tuple(names)

    def to_dict(self) -> dict:
        return {
            "date": self.date, "resolution": self.resolution, "as_of_time": self.as_of_time,
            "spot_available": self.spot_available, "futures_available": self.futures_available,
            "vix_available": self.vix_available, "options_available": self.options_available,
            "depth_available": self.depth_available,
            "certified_lineage_available": self.certified_lineage_available,
            "completeness": self.completeness, "is_complete": self.is_complete,
            "missing": list(self.missing), "fingerprint": self.fingerprint,
            "reconstruction_version": self.reconstruction_version,
            "options_ingestion_run_ids": list(self.options_ingestion_run_ids),
        }


def check_research_session_readiness(
    date: str,
    *,
    historical_store: HistoricalObservationStore,
    resolution: str = RESOLUTION_DAILY,
    as_of_time: Optional[str] = None,
) -> ResearchSessionReadiness:
    """Build the real snapshot (never raises for missing data, same
    contract as `build_market_reality_snapshot` itself) and restate its
    own presence facts as an explicit readiness report."""
    snapshot = build_market_reality_snapshot(
        date, historical_store=historical_store, resolution=resolution, as_of_time=as_of_time,
    )
    return ResearchSessionReadiness(
        date=date, resolution=resolution, as_of_time=snapshot.as_of,
        spot_available=snapshot.spot is not None,
        futures_available=snapshot.futures is not None,
        vix_available=snapshot.vix is not None,
        options_available=snapshot.options is not None and len(snapshot.options.contracts) > 0,
        depth_available=None,
        certified_lineage_available=len(snapshot.certification_refs) > 0,
        completeness=snapshot.completeness,
        fingerprint=snapshot.fingerprint(),
        reconstruction_version=snapshot.reconstruction_version,
        options_ingestion_run_ids=snapshot.options.ingestion_run_ids if snapshot.options else (),
    )
