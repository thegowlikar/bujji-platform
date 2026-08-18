"""Strategy Intelligence & Ranking Layer -- Phase 20.5.

Answers "given several validated strategy candidates, which deserves
trust?" -- NOT "what strategy should I build" (Phase 20.3/20.4) and
NOT "place/size a trade" (out of scope entirely; no broker dependency
anywhere in this package).

Reuses, unmodified:
- `bujji.epistemics.uncertainty` (Phase 16C/16D) -- the codebase's own
  canonical confidence-composition primitive (`Uncertainty`, `Input`,
  `compose()`, `demote()`). This package does NOT invent a second
  confidence scale; it composes this one.
- `bujji.strategy_research.stats.PerformanceStats` (Phase 20.4) shape
  informs `StrategyEvidence`'s fields, though this package accepts
  plain evidence records so it never has to import execution/research
  machinery to do its job.

Design principle, per this phase's own explicit instruction: MIC
context may only ever modify CONFIDENCE, never the underlying
evidence-based score -- "MIC must NOT become a hard strategy gate" is
the direct lesson Phase 20.4 drew from Trend Following's edge being
independent of MIC's own ~22% persistence accuracy. Proven directly
by test, not just documented (see `test_strategy_intelligence.py`).

See docs/PHASE_20_5_STRATEGY_INTELLIGENCE_REPORT.md.
"""
from .models import MarketContext, StrategyEvidence, StrategyRankingReport, StrategyScore
from .ranking import rank_strategies
from .scoring import score_strategy

__all__ = [
    "StrategyEvidence", "MarketContext", "StrategyScore", "StrategyRankingReport",
    "score_strategy", "rank_strategies",
]
