"""Phase 20.5 -- ranking. A single sort, nothing else -- no scoring
logic lives here (that's `scoring.py`'s job alone)."""
from __future__ import annotations

from typing import Sequence

from .models import StrategyRankingReport, StrategyScore


def rank_strategies(scores: Sequence[StrategyScore]) -> StrategyRankingReport:
    """Highest `effective_score` first. Ties broken by `sample_size`
    descending -- between two equally-scored strategies, the one with
    more real evidence behind it ranks higher, never an arbitrary or
    insertion-order tiebreak."""
    ranked = tuple(
        sorted(scores, key=lambda s: (s.effective_score, s.evidence.sample_size), reverse=True)
    )
    return StrategyRankingReport(scores=ranked)
