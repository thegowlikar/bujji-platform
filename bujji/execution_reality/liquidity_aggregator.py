"""Liquidity Aggregator -- Execution Reality Layer, Phase-1.

Combines one or more of LiquidityBrain's own, real `LiquidityReading`
outputs (one per CE/PE pair, produced by calling
`LiquidityBrain.analyze()` unchanged -- see liquidity_pairing_adapter.py
for how pairs are formed) into one structure-level `LiquidityHealthReading`.

OWNERSHIP, RESTATED: spread calculation, tightness classification,
confidence, evidence, and reasoning all remain LiquidityBrain's own,
exclusive output -- this module NEVER recomputes a spread or
reclassifies a pair's own tightness. It only combines already-computed
verdicts across pairs, via the worst-pair rule below. Every
`LiquidityReading` this module touches is carried through byte-for-byte,
never copied-with-changes.

NOT A DECISION: `LiquidityHealthReading` has no allowed/approved/
blocked/decision field anywhere in its schema (verified by this
module's own test suite's schema-safety check) -- it is a
classification artifact, structurally incapable of being consulted
as a gate, matching the same "remove the field, not just the intent"
enforcement already established throughout this design.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Tuple

from bujji.intelligence.models import DataQuality, LiquidityReading, SpreadTightness

Clock = Callable[[], datetime]

# Worst-first ranking: UNKNOWN is worse than WIDE, WIDE worse than NORMAL,
# NORMAL worse than TIGHT. Higher rank wins the aggregation.
_TIGHTNESS_RANK = {
    SpreadTightness.TIGHT: 0,
    SpreadTightness.NORMAL: 1,
    SpreadTightness.WIDE: 2,
    SpreadTightness.UNKNOWN: 3,
}

# Fail-closed: INSUFFICIENT outranks SUFFICIENT, same principle as tightness.
_DATA_QUALITY_RANK = {
    DataQuality.SUFFICIENT: 0,
    DataQuality.INSUFFICIENT: 1,
}


@dataclass(frozen=True)
class LiquidityHealthReading:
    """Thin wrapper over 1+ real LiquidityReading results for one
    multi-leg structure. No spread/tightness math of its own."""

    structure_id: str
    pair_readings: Tuple[Tuple[str, LiquidityReading], ...]   # (role_label, LiquidityReading), in caller-supplied order
    overall_tightness: SpreadTightness
    overall_data_quality: DataQuality
    as_of: datetime


def compute_liquidity_health(
    structure_id: str, pair_readings: Tuple[Tuple[str, LiquidityReading], ...], clock: Clock,
) -> LiquidityHealthReading:
    """`pair_readings`: already-computed (role_label, LiquidityReading)
    tuples -- each LiquidityReading must come from a real
    LiquidityBrain.analyze() call; this function never constructs one
    itself. Empty `pair_readings` is a caller error (a structure with
    zero pairs is nothing to aggregate) -- fails closed via ValueError
    rather than returning a fabricated "healthy" default."""
    if not pair_readings:
        raise ValueError("pair_readings must contain at least one (role_label, LiquidityReading) pair")

    worst_tightness = max(
        (reading.tightness for _, reading in pair_readings), key=lambda t: _TIGHTNESS_RANK[t],
    )
    worst_data_quality = max(
        (reading.data_quality for _, reading in pair_readings), key=lambda q: _DATA_QUALITY_RANK[q],
    )

    return LiquidityHealthReading(
        structure_id=structure_id, pair_readings=tuple(pair_readings),
        overall_tightness=worst_tightness, overall_data_quality=worst_data_quality, as_of=clock(),
    )
