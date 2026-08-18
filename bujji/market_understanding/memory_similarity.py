"""Market Memory Similarity Engine -- Phase 19.5.

Explainable, deterministic, no ML -- same discipline
`market_understanding/similarity.py`'s `compare()` already established
(Phase 17J.2: never treat a missing/differing dimension as anything
other than what it literally is, never fabricate a score), applied here
to a different feature space (`MarketMemoryEntry`'s six Intelligence
Core dimensions, not `SituationFeatureVector`'s 14 structure
dimensions -- see the Phase 19.5 audit doc for why the code itself is
not shared).

Every contribution is a plain, named rule -- never a black box:
`explain_similarity()` returns the exact per-dimension breakdown that
produced the final score, so "82% similar" always comes with "because."
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .memory_models import MarketMemoryEntry

# Positive weight per dimension when it matches exactly. Structure gets
# partial credit for "similar" (both near a wall, different side) since
# StructureProximity has a natural adjacency the other enums don't.
# Event is the one dimension with an explicit NEGATIVE contribution on
# mismatch (per this phase's own worked example: "Event: different = -5")
# -- event risk genuinely changes the situation's character more than a
# same-magnitude miss on the other dimensions.
WEIGHT_REGIME = 20
WEIGHT_VOLATILITY = 20
WEIGHT_STRUCTURE_SAME = 15
WEIGHT_STRUCTURE_SIMILAR = 7
WEIGHT_LIQUIDITY = 10
WEIGHT_EVENT_SAME = 10
WEIGHT_EVENT_DIFFERENT = -5
WEIGHT_EVIDENCE = 10

MAX_POSSIBLE_SCORE = (
    WEIGHT_REGIME + WEIGHT_VOLATILITY + WEIGHT_STRUCTURE_SAME
    + WEIGHT_LIQUIDITY + WEIGHT_EVENT_SAME + WEIGHT_EVIDENCE
)  # 85 -- the score every dimension matching exactly would produce.

_NEAR_PROXIMITIES = frozenset({"NEAR_RESISTANCE_WALL", "NEAR_SUPPORT_WALL"})

CONFIDENCE_BAND_LOW = "LOW"        # < 0.5
CONFIDENCE_BAND_MODERATE = "MODERATE"  # 0.5 - 0.8
CONFIDENCE_BAND_HIGH = "HIGH"      # >= 0.8


def _confidence_band(confidence: float) -> str:
    if confidence >= 0.8:
        return CONFIDENCE_BAND_HIGH
    if confidence >= 0.5:
        return CONFIDENCE_BAND_MODERATE
    return CONFIDENCE_BAND_LOW


@dataclass(frozen=True)
class SimilarityContribution:
    dimension: str
    label: str            # "same" | "similar" | "different" | "unknown"
    contribution: int

    def to_dict(self) -> dict:
        return {"dimension": self.dimension, "label": self.label, "contribution": self.contribution}


@dataclass(frozen=True)
class SimilarityExplanation:
    """Never a black box: `breakdown` alone reproduces `similarity_pct`
    -- `round(100 * max(0, sum(c.contribution for c in breakdown)) / MAX_POSSIBLE_SCORE, 1)`.
    A caller never has to trust the score without being able to verify it."""

    candidate_market_memory_id: str
    similarity_pct: float
    breakdown: Tuple[SimilarityContribution, ...]

    def to_dict(self) -> dict:
        return {
            "candidate_market_memory_id": self.candidate_market_memory_id,
            "similarity_pct": self.similarity_pct,
            "breakdown": [c.to_dict() for c in self.breakdown],
        }


def _dimension_contribution(dimension: str, target_value, candidate_value, *, same_weight: int) -> SimilarityContribution:
    if target_value is None or candidate_value is None:
        return SimilarityContribution(dimension, "unknown", 0)
    if target_value == candidate_value:
        return SimilarityContribution(dimension, "same", same_weight)
    return SimilarityContribution(dimension, "different", 0)


def _structure_contribution(target: MarketMemoryEntry, candidate: MarketMemoryEntry) -> SimilarityContribution:
    a, b = target.structure_proximity, candidate.structure_proximity
    if a is None or b is None:
        return SimilarityContribution("structure", "unknown", 0)
    if a == b:
        return SimilarityContribution("structure", "same", WEIGHT_STRUCTURE_SAME)
    if a in _NEAR_PROXIMITIES and b in _NEAR_PROXIMITIES:
        return SimilarityContribution("structure", "similar", WEIGHT_STRUCTURE_SIMILAR)
    return SimilarityContribution("structure", "different", 0)


def _event_contribution(target: MarketMemoryEntry, candidate: MarketMemoryEntry) -> SimilarityContribution:
    a = (target.event_expiry_proximity, target.event_vix_regime)
    b = (candidate.event_expiry_proximity, candidate.event_vix_regime)
    if a[0] is None or a[1] is None or b[0] is None or b[1] is None:
        return SimilarityContribution("event", "unknown", 0)
    if a == b:
        return SimilarityContribution("event", "same", WEIGHT_EVENT_SAME)
    return SimilarityContribution("event", "different", WEIGHT_EVENT_DIFFERENT)


def _evidence_contribution(target: MarketMemoryEntry, candidate: MarketMemoryEntry) -> SimilarityContribution:
    band_a, band_b = _confidence_band(target.confidence), _confidence_band(candidate.confidence)
    if band_a == band_b:
        return SimilarityContribution("evidence", "same", WEIGHT_EVIDENCE)
    return SimilarityContribution("evidence", "different", 0)


def explain_similarity(target: MarketMemoryEntry, candidate: MarketMemoryEntry) -> SimilarityExplanation:
    breakdown = (
        _dimension_contribution("regime", target.regime_state, candidate.regime_state, same_weight=WEIGHT_REGIME),
        _dimension_contribution("volatility", target.volatility_richness, candidate.volatility_richness, same_weight=WEIGHT_VOLATILITY),
        _structure_contribution(target, candidate),
        _dimension_contribution("liquidity", target.liquidity_tightness, candidate.liquidity_tightness, same_weight=WEIGHT_LIQUIDITY),
        _event_contribution(target, candidate),
        _evidence_contribution(target, candidate),
    )
    raw_score = sum(c.contribution for c in breakdown)
    similarity_pct = round(100 * max(0, raw_score) / MAX_POSSIBLE_SCORE, 1)
    return SimilarityExplanation(
        candidate_market_memory_id=candidate.market_memory_id,
        similarity_pct=similarity_pct, breakdown=breakdown,
    )


def find_similar_memories(
    target: MarketMemoryEntry, candidates: List[MarketMemoryEntry], *, top_n: int = 5,
) -> List[Tuple[MarketMemoryEntry, SimilarityExplanation]]:
    """Ranks `candidates` by similarity to `target`, descending. Excludes
    `target` itself (by `market_memory_id`) -- a moment is not "similar
    to itself" in any useful sense here, same discipline
    `market_understanding.similarity.find_similar()` already established."""
    scored = []
    for candidate in candidates:
        if candidate.market_memory_id == target.market_memory_id:
            continue
        explanation = explain_similarity(target, candidate)
        scored.append((candidate, explanation))
    scored.sort(key=lambda pair: pair[1].similarity_pct, reverse=True)
    return scored[:top_n]
