"""Reality Memory Event models — Phase 17J.1. Models only, no IO.

NAMED `RealityMemoryEvent`, NOT `MarketMemory`/`Memory` bare -- per
`docs/PHASE_17J0_MARKET_MEMORY_BOUNDARY_AUDIT.md` §1: `ObservationMemory`
(`bujji.market_state_builder`) and `RegimeMemoryState`/
`market_regime_memory` (Phase 11) already use "Memory" for
session-scoped, Intelligence-tier concepts that reset with the process.
`RealityMemoryEvent` is durable and cross-year, sourced from the full
certified Reality corpus -- a different concept, so it gets a different
name, not a reused one.

REALITY-ONLY, per the explicit decision recorded in
PHASE_17J0 §4 (2026-08-14): every field here is a LITERAL fact read
directly from an already-built `MarketRealitySnapshot` -- no
percentage change, no basis-in-points, no drawdown-from-high, no
normalization of any kind. `VixSnapshot.change_percent` exists on the
source model but is deliberately NOT projected here, even though it is
currently always `None` in practice -- excluding it at the type level
(not just relying on it staying unset upstream) is what makes this
model's Reality-only guarantee structural rather than incidental.

NO NEW STORE. A `RealityMemoryEvent` is built by pure transformation
from an already-existing `MarketRealitySnapshot` row
(`market_reality_snapshot.reconstruction`/`store`, Phase 17H.5-17H.7) --
this module holds no persistence of its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from bujji.market_reality_snapshot.models import MarketRealitySnapshot

SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class RealityMemoryEvent:
    """One date's Reality-only situation record. Every numeric field is
    either a literal stored value or `None` -- `None` means the fact
    genuinely was not captured for this date (no fake zero, no
    carried-forward value, matching `MarketRealitySnapshot`'s own
    no-fake-completeness rule)."""

    date: str  # "YYYY-MM-DD"

    spot_open: Optional[float]
    spot_high: Optional[float]
    spot_low: Optional[float]
    spot_close: Optional[float]
    spot_volume: Optional[float]
    spot_source: Optional[str]  # SOURCE_HISTORICAL | SOURCE_LIVE | None (spot absent)

    futures_instrument_identity: Optional[str]  # "NIFTY_FUT_CONTINUOUS" or the real live contract symbol
    futures_open: Optional[float]
    futures_high: Optional[float]
    futures_low: Optional[float]
    futures_close: Optional[float]
    futures_expiry_date: Optional[str]
    futures_source: Optional[str]

    vix_open: Optional[float]
    vix_high: Optional[float]
    vix_low: Optional[float]
    vix_close: Optional[float]
    vix_source: Optional[str]

    completeness: str  # ALL_COMPLETENESS_STATES, from the source snapshot
    is_final: bool
    source_observation_ids: Tuple[str, ...]
    certification_refs: Tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    @staticmethod
    def from_snapshot(snap: MarketRealitySnapshot) -> "RealityMemoryEvent":
        """Pure transformation -- every value here traces to a field
        already present on `snap`. Nothing is computed."""
        spot = snap.spot
        futures = snap.futures
        vix = snap.vix
        return RealityMemoryEvent(
            date=snap.date,
            spot_open=spot.open if spot else None,
            spot_high=spot.high if spot else None,
            spot_low=spot.low if spot else None,
            spot_close=spot.close if spot else None,
            spot_volume=spot.volume if spot else None,
            spot_source=spot.source if spot else None,
            futures_instrument_identity=futures.instrument if futures else None,
            futures_open=futures.open if futures else None,
            futures_high=futures.high if futures else None,
            futures_low=futures.low if futures else None,
            futures_close=futures.close if futures else None,
            futures_expiry_date=futures.expiry_date if futures else None,
            futures_source=futures.source if futures else None,
            vix_open=vix.open if vix else None,
            vix_high=vix.high if vix else None,
            vix_low=vix.low if vix else None,
            vix_close=vix.close if vix else None,
            vix_source=vix.source if vix else None,
            completeness=snap.completeness,
            is_final=snap.is_final,
            source_observation_ids=snap.source_observation_ids,
            certification_refs=snap.certification_refs,
        )

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "spot_open": self.spot_open, "spot_high": self.spot_high,
            "spot_low": self.spot_low, "spot_close": self.spot_close,
            "spot_volume": self.spot_volume, "spot_source": self.spot_source,
            "futures_instrument_identity": self.futures_instrument_identity,
            "futures_open": self.futures_open, "futures_high": self.futures_high,
            "futures_low": self.futures_low, "futures_close": self.futures_close,
            "futures_expiry_date": self.futures_expiry_date, "futures_source": self.futures_source,
            "vix_open": self.vix_open, "vix_high": self.vix_high,
            "vix_low": self.vix_low, "vix_close": self.vix_close, "vix_source": self.vix_source,
            "completeness": self.completeness, "is_final": self.is_final,
            "source_observation_ids": list(self.source_observation_ids),
            "certification_refs": list(self.certification_refs),
            "schema_version": self.schema_version,
        }

    @staticmethod
    def from_dict(d: Mapping) -> "RealityMemoryEvent":
        return RealityMemoryEvent(
            date=d["date"],
            spot_open=d.get("spot_open"), spot_high=d.get("spot_high"),
            spot_low=d.get("spot_low"), spot_close=d.get("spot_close"),
            spot_volume=d.get("spot_volume"), spot_source=d.get("spot_source"),
            futures_instrument_identity=d.get("futures_instrument_identity"),
            futures_open=d.get("futures_open"), futures_high=d.get("futures_high"),
            futures_low=d.get("futures_low"), futures_close=d.get("futures_close"),
            futures_expiry_date=d.get("futures_expiry_date"), futures_source=d.get("futures_source"),
            vix_open=d.get("vix_open"), vix_high=d.get("vix_high"),
            vix_low=d.get("vix_low"), vix_close=d.get("vix_close"), vix_source=d.get("vix_source"),
            completeness=d["completeness"], is_final=d["is_final"],
            source_observation_ids=tuple(d.get("source_observation_ids") or ()),
            certification_refs=tuple(d.get("certification_refs") or ()),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )
