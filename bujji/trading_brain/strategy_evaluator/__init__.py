"""Strategy Candidate Evaluator — BUJJI Options OS v3, Trading Brain
Intelligence Upgrade, Phase 1.

Scores and ranks the strategies `bujji.trading_brain.strategy_selector`
already found eligible. Never re-gates, never modifies that module.
See engine.py for the full design rationale.
"""
from .engine import rank
from .models import DimensionScore, RankedCandidates, StrategyRanking
from . import taxonomy

__all__ = ["rank", "DimensionScore", "RankedCandidates", "StrategyRanking", "taxonomy"]
