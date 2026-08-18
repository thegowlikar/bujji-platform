"""Situation Similarity — Understanding tier. Phase 17J.2 (revisit).

Per `docs/PHASE_17J2_REVISIT_STRUCTURE_SIMILARITY_DESIGN.md` §4a
(decided 2026-08-14): compares dates using the 14 already-classified
`IntradayStructureRecord` dimensions (17G.A) plus a bucketed VIX level
(a Reality-tier literal fact, from `RealityMemoryEvent`, 17J.1) --
never raw arithmetic (% change, basis points), the exact category of
value this project's Reality/Memory tiers have forbidden since 17E.

DISTANCE METRIC: exact-match fraction across dimensions comparable on
BOTH sides -- chosen over an ordinal-ranking scheme specifically to
avoid needing 14 separate, undefended ordinal judgments (design doc
§4). A dimension missing on either side is excluded from that specific
comparison's denominator, never treated as a match or a mismatch by
default -- absence is never a value.

Explicitly NOT built here, per every restriction stated across
17J.0/17J.2/17GA*: no prediction, no probability, no strategy
selection, no "this means X" label. `find_similar()` returns dates and
a numeric similarity score -- nothing about what happened next, and
nothing about what to do about it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from bujji.market_understanding.structure import IntradayStructureCatalog, IntradayStructureRecord
from bujji.reality_memory.catalog import RealityMemoryCatalog

SCHEMA_VERSION = "1.0.0"

# Disclosed, first-pass VIX bands -- same convention as
# `bujji.intelligence.volatility_brain.RICHNESS_RICH_THRESHOLD`: a
# stated starting point, not a statistically calibrated conclusion.
VIX_BAND_LOW = "LOW"              # < 12
VIX_BAND_NORMAL = "NORMAL"        # 12-15
VIX_BAND_ELEVATED = "ELEVATED"    # 15-20
VIX_BAND_HIGH = "HIGH"            # 20-30
VIX_BAND_EXTREME = "EXTREME"      # >= 30


def vix_band(vix_close: Optional[float]) -> Optional[str]:
    if vix_close is None:
        return None
    if vix_close < 12:
        return VIX_BAND_LOW
    if vix_close < 15:
        return VIX_BAND_NORMAL
    if vix_close < 20:
        return VIX_BAND_ELEVATED
    if vix_close < 30:
        return VIX_BAND_HIGH
    return VIX_BAND_EXTREME


# The 14 IntradayStructureRecord dimensions + the bucketed VIX band --
# every dimension `compare()` ever looks at, named explicitly rather
# than iterated by reflection, so the comparable set is auditable at a
# glance.
STRUCTURE_DIMENSIONS = (
    "trend_state", "swing_state", "compression_state", "expansion_state",
    "balance_state", "structure_state", "structure_integrity",
    "support_state", "resistance_state", "breakout_state", "breakdown_state",
    "retest_state", "rejection_state", "structural_balance", "structure_location",
)
ALL_DIMENSIONS = STRUCTURE_DIMENSIONS + ("vix_band",)


@dataclass(frozen=True)
class SituationFeatureVector:
    """One instant's comparable situation -- 14 Understanding-tier
    structure classifications plus one bucketed Reality-tier VIX band.
    `vix_close` is carried for reference/lineage only, never compared
    directly (comparison always goes through `vix_band`)."""

    instrument_identity: str
    date: str
    as_of: str
    trend_state: Optional[str]
    swing_state: Optional[str]
    compression_state: Optional[str]
    expansion_state: Optional[str]
    balance_state: Optional[str]
    structure_state: Optional[str]
    structure_integrity: Optional[str]
    support_state: Optional[str]
    resistance_state: Optional[str]
    breakout_state: Optional[str]
    breakdown_state: Optional[str]
    retest_state: Optional[str]
    rejection_state: Optional[str]
    structural_balance: Optional[str]
    structure_location: Optional[str]
    vix_close: Optional[float]
    vix_band: Optional[str]
    source_observation_ids: Tuple[str, ...]
    schema_version: str = SCHEMA_VERSION


def build_feature_vector(
    instrument_identity: str, date: str, as_of_timestamp: str, *,
    structure_catalog: IntradayStructureCatalog, memory_catalog: RealityMemoryCatalog,
) -> Optional[SituationFeatureVector]:
    """Composes an `IntradayStructureRecord` (17G.A) with the same
    date's `RealityMemoryEvent` VIX close (17J.1) into one comparable
    vector. Returns None if structure data is unavailable for the
    instant -- never a placeholder vector with fabricated dimensions."""
    structure: Optional[IntradayStructureRecord] = structure_catalog.get(
        instrument_identity, as_of_timestamp,
    )
    if structure is None:
        return None

    memory_event = memory_catalog.get(date)
    vix_close = memory_event.vix_close if memory_event is not None else None

    return SituationFeatureVector(
        instrument_identity=instrument_identity, date=date, as_of=as_of_timestamp,
        trend_state=structure.price_structure.trend_state,
        swing_state=structure.price_structure.swing_state,
        compression_state=structure.price_structure.compression_state,
        expansion_state=structure.price_structure.expansion_state,
        balance_state=structure.price_structure.balance_state,
        structure_state=structure.price_structure.structure_state,
        structure_integrity=structure.price_structure.structure_integrity,
        support_state=structure.market_structure.support_state,
        resistance_state=structure.market_structure.resistance_state,
        breakout_state=structure.market_structure.breakout_state,
        breakdown_state=structure.market_structure.breakdown_state,
        retest_state=structure.market_structure.retest_state,
        rejection_state=structure.market_structure.rejection_state,
        structural_balance=structure.market_structure.structural_balance,
        structure_location=structure.market_structure.structure_location,
        vix_close=vix_close,
        vix_band=vix_band(vix_close),
        source_observation_ids=structure.supporting_observation_ids,
    )


def compare(a: SituationFeatureVector, b: SituationFeatureVector) -> Optional[float]:
    """Exact-match fraction over dimensions present on BOTH vectors.
    Returns None (never 0.0) if zero dimensions are comparable -- an
    undefined comparison is not the same fact as "zero similarity"."""
    comparable = 0
    matches = 0
    for dim in ALL_DIMENSIONS:
        va, vb = getattr(a, dim), getattr(b, dim)
        if va is None or vb is None:
            continue
        comparable += 1
        if va == vb:
            matches += 1
    if comparable == 0:
        return None
    return matches / comparable


def find_similar(
    target: SituationFeatureVector, candidates: List[SituationFeatureVector], *,
    top_n: int = 5, exclude_self: bool = True,
) -> List[Tuple[SituationFeatureVector, float]]:
    """Ranks `candidates` by similarity to `target`, descending.
    Candidates with an undefined comparison (`compare()` returns None)
    are excluded, never scored as 0.0. `exclude_self` drops a candidate
    whose (instrument_identity, as_of) exactly matches `target`'s own --
    a date is not "similar to itself" in any useful sense here."""
    scored: List[Tuple[SituationFeatureVector, float]] = []
    for candidate in candidates:
        if exclude_self and candidate.instrument_identity == target.instrument_identity \
                and candidate.as_of == target.as_of:
            continue
        score = compare(target, candidate)
        if score is not None:
            scored.append((candidate, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_n]
