"""Intraday Structure — Understanding tier. Phase 17G.A/17J.1-revisit.

Explicitly NOT Reality, NOT Memory. `RealityMemoryEvent` (17J.1) is
contractually Reality-only (its own docstring: "no normalization of any
kind") per the decision recorded in
`docs/PHASE_17J0_MARKET_MEMORY_BOUNDARY_AUDIT.md` §4 -- adding
classified structure fields (trend/swing/support/resistance) to it
would silently reverse that decision. This module exists precisely so
that never has to happen: intraday structure lives here, one tier up,
clearly labeled, never merged into the Reality-only model.

NAMED to avoid "Memory" entirely, unlike `RealityMemoryEvent` -- per
`PHASE_17J0`'s own finding that "Memory" already means two different
things in this codebase (`ObservationMemory`, `market_regime_memory`,
both session-scoped, Intelligence-tier). This package is Understanding-
tier and durable-corpus-scoped (like Reality/Memory), so reusing
"Memory" here would create a third, different meaning for the same
word. `bujji.market_understanding` matches the name this project's own
prior design document
(`docs/PHASE_17G_MARKET_UNDERSTANDING_ARCHITECTURE_REVIEW.md`) already
uses for this exact tier.

NO NEW STORE: composes `reality_structure_bridge.structure_as_of()`
(17G.A) on demand, same "recompute rather than cache" precedent as
`RealityMemoryCatalog` (17J.1) and `RealityCoverageIndex` (17I.4).
Persisting a continuous structure stream remains a separate, later,
explicit decision -- not made here, not a side effect of this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from bujji.historical_reality.store import HistoricalObservationStore
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_price_structure.models import PriceStructureAssessment
from bujji.reality_structure_bridge.bridge import DEFAULT_LOOKBACK_BARS, structure_as_of

SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class IntradayStructureRecord:
    """One instrument's classified structure state as of one instant.
    UNDERSTANDING-TIER: every field here is a DERIVED classification
    (trend/swing/support/resistance/etc.), never a literal Reality
    fact -- the opposite guarantee from `RealityMemoryEvent`, stated
    explicitly so the two are never confused."""

    instrument_identity: str
    as_of: str
    price_structure: PriceStructureAssessment
    market_structure: MarketStructureAssessment
    schema_version: str = SCHEMA_VERSION

    @property
    def supporting_observation_ids(self) -> Tuple[str, ...]:
        """Full lineage back to the Reality-tier `HistoricalObservation`
        ids this record was computed from -- required so a consumer can
        always trace a classification back to real, certified facts."""
        return tuple(sorted(set(
            self.price_structure.supporting_observation_ids
        ) | set(
            self.market_structure.supporting_observation_ids
        )))

    def to_dict(self) -> dict:
        return {
            "instrument_identity": self.instrument_identity,
            "as_of": self.as_of,
            "trend_state": self.price_structure.trend_state,
            "trend_direction_signal": self.price_structure.trend_direction_signal,
            "swing_state": self.price_structure.swing_state,
            "compression_state": self.price_structure.compression_state,
            "expansion_state": self.price_structure.expansion_state,
            "balance_state": self.price_structure.balance_state,
            "structure_state": self.price_structure.structure_state,
            "structure_integrity": self.price_structure.structure_integrity,
            "price_confidence": self.price_structure.confidence,
            "support_state": self.market_structure.support_state,
            "resistance_state": self.market_structure.resistance_state,
            "breakout_state": self.market_structure.breakout_state,
            "breakdown_state": self.market_structure.breakdown_state,
            "retest_state": self.market_structure.retest_state,
            "rejection_state": self.market_structure.rejection_state,
            "structural_balance": self.market_structure.structural_balance,
            "structure_location": self.market_structure.structure_location,
            "market_confidence": self.market_structure.confidence,
            "supporting_observation_ids": list(self.supporting_observation_ids),
            "schema_version": self.schema_version,
        }


class IntradayStructureCatalog:
    """Read-only. Never writes to any store. Returns `None` when there
    is insufficient Reality data for the requested instant -- never a
    placeholder/degenerate record (matches `structure_as_of()`'s own
    honest-absence contract)."""

    def __init__(self, *, historical_store: HistoricalObservationStore) -> None:
        self._historical_store = historical_store

    def get(
        self, instrument_identity: str, as_of_timestamp: str, *,
        lookback_bars: int = DEFAULT_LOOKBACK_BARS,
    ) -> Optional[IntradayStructureRecord]:
        price, market = structure_as_of(
            instrument_identity, as_of_timestamp,
            historical_store=self._historical_store, lookback_bars=lookback_bars,
        )
        if price is None or market is None:
            return None
        return IntradayStructureRecord(
            instrument_identity=instrument_identity, as_of=as_of_timestamp,
            price_structure=price, market_structure=market,
        )
