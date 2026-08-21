"""Opportunity Qualification Engine -- Phase 20.6.

Answers "given today's market conditions, should this strategy
actually be considered deployable now?" -- the bridge between Phase
20.5's evidence-based ranking and a (not-yet-built) Risk Governor /
Execution layer. No order placement, no position sizing, no broker
dependency anywhere in this package.

Reuses, unmodified:
- `bujji.strategy_intelligence.StrategyScore` (Phase 20.5) -- consumed
  as-is; `evidence_score`/`confidence`/`effective_score` are NEVER
  recalculated here.
- `bujji.mic_v0.models` vocabulary (`RISK_*`, `VOLATILITY_*`, Phase
  20.1) and `bujji.mic_v0_validation.models_intraday` vocabulary
  (`INTRADAY_*`, Phase 20.1C) for market regime/risk/volatility
  labels.
- `bujji.strategy_research.eligibility`-style favorable/unfavorable
  regime declarations (the same pattern Phase 20.3 already
  established) as the caller-supplied compatibility data this
  package's rules consult -- never inferred or hardcoded here.

Design precedent followed, not code reused: `bujji.decision_context.
compatibility_engine.StrategyCompatibilityEngine` (Phase 19.4)
established this codebase's own "declare UNASSESSED/no-signal rather
than force a compatibility guess" discipline and "one hard blocker
overrides everything" gate pattern for a DIFFERENT domain (options
strategy families via MarketIntelligenceSnapshot) -- this package
follows the same discipline for MIC-v0-driven Cycle-1 strategies,
without importing any of that engine's code (wrong domain, confirmed
in this phase's own audit).

See docs/PHASE_20_6_OPPORTUNITY_QUALIFICATION_REPORT.md.
"""
from .evaluator import evaluate_opportunity
from .models import (
    ALL_QUALIFICATION_STATES,
    BLOCKED,
    ELIGIBLE,
    INSUFFICIENT_EVIDENCE,
    WATCH,
    MarketEnvironment,
    OpportunityAssessment,
    QualificationDecision,
    QualificationReason,
)
from .rules import qualification_rules

__all__ = [
    "ELIGIBLE", "WATCH", "BLOCKED", "INSUFFICIENT_EVIDENCE", "ALL_QUALIFICATION_STATES",
    "MarketEnvironment", "QualificationReason", "QualificationDecision", "OpportunityAssessment",
    "evaluate_opportunity", "qualification_rules",
]
