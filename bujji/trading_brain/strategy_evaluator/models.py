"""Strategy Candidate Evaluator models — frozen, immutable ranking
records.

Nothing here sizes a position, calculates lots, places an order, or
estimates PnL -- identical discipline to strategy_selector.models.
`RankedCandidates` never invents a strategy outside the set
strategy_selector already found ELIGIBLE.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class DimensionScore:
    """One dimension's score for one strategy -- always a named level
    plus a stated, human-readable reason. Never a bare number."""

    dimension: str
    level: str
    reason: str


@dataclass(frozen=True)
class StrategyRanking:
    """One eligible strategy's full scorecard and resulting rank."""

    strategy_id: str
    dimension_scores: Tuple[DimensionScore, ...]
    high_count: int
    unknown_count: int
    rank_position: int  # 1 = best. Ties share equal high_count/unknown_count, broken by registry declaration order.
    summary: str


@dataclass(frozen=True)
class RankedCandidates:
    ranking_id: str
    strategy_decision_id: Optional[str]
    status: str
    winner: Optional[str]
    rankings: Tuple[StrategyRanking, ...]  # eligible candidates only, best (rank_position=1) first
    rejected_ineligible: Tuple[str, ...]  # strategy_ids strategy_selector already rejected -- carried through unscored
    decision_trace: str
    timestamp: str
    version: str
