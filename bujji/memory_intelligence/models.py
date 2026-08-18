"""Phase 20.15.1 -- pure data contracts. No IO, no broker, no
execution, no strategy-scoring/tuning logic anywhere in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

DIRECTION_SUPPORTED = "SUPPORTED"        # strong favorable history -> confidence may rise, one band, capped.
DIRECTION_CONTRADICTED = "CONTRADICTED"  # strong unfavorable history -> confidence demoted, one band.
DIRECTION_UNCHANGED = "UNCHANGED"        # insufficient or mixed history -> confidence untouched.
ALL_DIRECTIONS = (DIRECTION_SUPPORTED, DIRECTION_CONTRADICTED, DIRECTION_UNCHANGED)

FLAG_INSUFFICIENT_SIMILAR = "INSUFFICIENT_SIMILAR_CONDITIONS"
FLAG_INSUFFICIENT_KNOWN_OUTCOMES = "INSUFFICIENT_KNOWN_OUTCOMES"
FLAG_CONTRADICTORY_HISTORY = "CONTRADICTORY_HISTORICAL_OUTCOMES"
FLAG_NO_MEMORY = "NO_MEMORY_AVAILABLE"


@dataclass(frozen=True)
class MemoryInfluenceAssessment:
    """The ONLY output of `bujji.memory_intelligence`. Carries a
    `confidence_modifier` a caller MAY apply to a `StrategyScore`'s own
    `confidence` -- never `evidence_score`/`effective_score`, which are
    not fields on this object at all, so no caller can mistake this for
    a second score. `base_confidence`/`confidence_modifier` being equal
    IS the honest "no adjustment" case -- never a sentinel."""

    strategy_name: str
    memory_available: bool
    similarity_count: int
    historical_outcome_summary: Dict[str, int]   # {"favorable": n, "unfavorable": m} of KNOWN outcomes only.
    base_confidence: str
    confidence_modifier: str                      # the resulting confidence -- base_confidence, +1 band, or -1 band.
    confidence_direction: str                      # ALL_DIRECTIONS
    uncertainty_flags: Tuple[str, ...] = field(default_factory=tuple)
    explanation: str = ""

    def __post_init__(self) -> None:
        if self.confidence_direction not in ALL_DIRECTIONS:
            raise ValueError(f"confidence_direction={self.confidence_direction!r} not in {ALL_DIRECTIONS}")

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name, "memory_available": self.memory_available,
            "similarity_count": self.similarity_count,
            "historical_outcome_summary": dict(self.historical_outcome_summary),
            "base_confidence": self.base_confidence, "confidence_modifier": self.confidence_modifier,
            "confidence_direction": self.confidence_direction,
            "uncertainty_flags": list(self.uncertainty_flags), "explanation": self.explanation,
        }
