"""Portfolio Intelligence & Multi-Opportunity Conflict Resolution --
Phase 20.9.

NAMING NOTE, disclosed prominently because it changed this package's
name: the requested path `bujji/portfolio_intelligence/` already
exists -- built in Phase 15M, a real, unrelated engine aggregating
Greek exposure, underlying/expiry concentration, and thesis health
across REAL, OPEN options positions (`PositionLifecycle`). This
phase's own subject -- pre-trade compatibility between Cycle-1
STRATEGY OPPORTUNITIES, never a real position -- is a different
problem entirely; overwriting or extending that package in place
would conflate two unrelated systems. This package is named
`bujji.opportunity_portfolio` instead, following the same "disclosed
collision, distinct name" precedent already established for `mic_v0`
(vs the pre-existing "Market Intelligence Core" brains) and
`strategy_research` (vs the pre-existing `msi_strategy_*` series).

Answers "what happens when multiple qualified opportunities compete
for limited portfolio attention?" -- NOT execution, NOT capital
deployment, NOT position sizing, NOT margin management. Output is
portfolio PREFERENCE and CONFLICT RESOLUTION only; no quantity,
capital amount, margin figure, broker call, or order exists anywhere
in this package.

Reuses, unmodified:
- `bujji.capital_intelligence.AllocationAssessment` (Phase 20.8) --
  every input (`priority_score`, `rank`, `allocation_class`,
  `candidate.assessment.strategy_score`, `.environment`) is read
  transitively from this single type, never recalculated.
- `bujji.strategy_research.ALL_FAMILIES` (Phase 20.3) -- each family's
  own already-declared `market_conditions_required`/
  `incompatible_conditions` is the SOLE source of truth for whether
  two strategies expect opposite market behavior; no new conflict
  rule is invented, only Phase 20.3's own disclosed declarations are
  read.

See docs/PHASE_20_9_PORTFOLIO_INTELLIGENCE_REPORT.md.
"""
from .conflict import evaluate_conflicts
from .explain import explain_portfolio_decision
from .models import (
    ALL_CONFLICT_STATES,
    ALL_PORTFOLIO_DECISIONS,
    ALLOW_MULTIPLE,
    COMPATIBLE,
    CONFLICTING,
    EXCLUSIVE,
    INSUFFICIENT_EVIDENCE,
    NEUTRAL,
    NO_SELECTION,
    REDUCE_CONFLICT,
    SELECT_PRIMARY,
    CorrelationAssessment,
    OpportunityConflict,
    PortfolioDecision,
)
from .portfolio import rank_portfolio_choices

__all__ = [
    "COMPATIBLE", "NEUTRAL", "CONFLICTING", "EXCLUSIVE", "ALL_CONFLICT_STATES",
    "SELECT_PRIMARY", "ALLOW_MULTIPLE", "REDUCE_CONFLICT", "NO_SELECTION", "INSUFFICIENT_EVIDENCE",
    "ALL_PORTFOLIO_DECISIONS",
    "OpportunityConflict", "CorrelationAssessment", "PortfolioDecision",
    "evaluate_conflicts", "rank_portfolio_choices", "explain_portfolio_decision",
]
