"""Opportunity Ranking Engine -- Phase 20.7.

Answers "among today's qualified opportunities, which deserves
priority?" -- NOT capital allocation, NOT position sizing, NOT
portfolio optimization, NOT execution. No trade-sizing arithmetic, and
no order/broker vocabulary, exists anywhere in this package.

Reuses, unmodified:
- `bujji.opportunity_intelligence.OpportunityAssessment` (Phase 20.6)
  -- every ranking input (`strategy_score`, `environment`, `decision`)
  is read from this, never recalculated.
- `bujji.strategy_intelligence.StrategyScore` (Phase 20.5), transitively.

Step 1 audit (this phase) found no existing opportunity-ranking
engine to duplicate: `bujji.msi_dynamic_management.query.
priority_rank`/`highest_priority_decision` ranks six POSITION-
MANAGEMENT actions (strike roll, expiry roll, ...) for a single
already-open options position -- a different ranking problem
entirely, not reused. `bujji.strategy_intelligence.ranking.
rank_strategies` (Phase 20.5) sorts raw evidence with no qualification
awareness; this phase needed qualification-aware exclusion
(BLOCKED/INSUFFICIENT_EVIDENCE never rank), so a new, small function
was built rather than stretching that one to a second purpose.

See docs/PHASE_20_7_OPPORTUNITY_RANKING_REPORT.md.
"""
from .explain import explain_ranking
from .models import (
    ExcludedOpportunity, OpportunityCandidate, PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MEDIUM,
    RankedOpportunity, RankingResult,
)
from .scorer import rank_opportunities

__all__ = [
    "OpportunityCandidate", "RankedOpportunity", "ExcludedOpportunity", "RankingResult",
    "PRIORITY_HIGH", "PRIORITY_MEDIUM", "PRIORITY_LOW",
    "rank_opportunities", "explain_ranking",
]
