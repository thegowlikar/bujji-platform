"""Trade Liquidity Context -- Execution Reality Layer, Phase-2.

PURPOSE: the immutable transport artifact that carries Phase-1's
`LiquidityHealthReading` (and the Phase-0 quote timestamps behind it)
forward, for a FUTURE phase to reference by `structure_id` -- exactly
mirroring msi_trade_construction's own existing
`TradeConstructionAssessment.supporting_assessment_ids` convention
(reference by ID, never embed-and-duplicate). Nothing in this module
has a consumer yet: it is a standalone artifact layer, not wired into
MSI, the Governor, or anything else.

NO CLOCK, NO datetime.now(), DELIBERATELY: `created_at` is always
caller-supplied. Every other timestamp-bearing type in this session's
architecture (LegQuote, LiquidityReading, TradeConstructionAssessment)
either takes an injected Clock or a caller-supplied ISO string -- this
module follows the same discipline, one step further: it doesn't even
accept a Clock, because it MUST NOT be able to generate a timestamp of
its own under any circumstance. If a caller doesn't supply `created_at`,
construction fails -- there is no fallback.

FRESHNESS IS NOT EVALUATED HERE: `quote_observation_timestamps` and
`liquidity_health.as_of` are preserved exactly as given, never compared
against "now," never classified as fresh/stale. That evaluation is
explicitly Governor-owned future work (see the Phase-2 design review),
not this module's job.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

from bujji.execution_reality.liquidity_aggregator import LiquidityHealthReading
from bujji.execution_reality.models import LegQuote
from bujji.intelligence.models import DataQuality


@dataclass(frozen=True)
class TradeLiquidityContext:
    """Pure transport -- no decision semantics, no consumer wired up
    yet. Every field is either supplied verbatim by the caller or
    extracted, unmodified, from an already-real Phase-0/Phase-1
    object. Nothing here computes a spread, classifies liquidity, or
    evaluates freshness."""

    structure_id: str
    liquidity_health: LiquidityHealthReading
    quote_observation_timestamps: Tuple[Tuple[str, str], ...]   # (symbol, LegQuote.timestamp), one per leg
    data_quality: DataQuality
    created_at: str


def build_trade_liquidity_context(
    structure_id: str, liquidity_health: LiquidityHealthReading, legs: Sequence[LegQuote], created_at: str,
) -> TradeLiquidityContext:
    """The only permitted construction path. Extracts quote
    timestamps and overall data quality from already-computed
    Phase-0/Phase-1 objects -- never evaluates freshness, never
    classifies liquidity, never approves/rejects anything, never
    modifies `liquidity_health` or any `LegQuote`, never calculates a
    spread. `created_at` is required and used exactly as given."""
    quote_observation_timestamps = tuple(
        (leg.symbol, leg.timestamp) for leg in legs if leg.timestamp is not None
    )
    return TradeLiquidityContext(
        structure_id=structure_id, liquidity_health=liquidity_health,
        quote_observation_timestamps=quote_observation_timestamps,
        data_quality=liquidity_health.overall_data_quality, created_at=created_at,
    )
