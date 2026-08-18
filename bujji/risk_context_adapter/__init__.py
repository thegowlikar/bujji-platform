"""Phase 20.17.1 -- Risk Context Adapter.

Fixes the architectural mismatch Phase 20.17 discovered: Cycle 1 asks
"can this NEW opportunity be safely admitted?" while the real Trading
Brain Risk Governor's D.2 (portfolio_risk_aggregator) expects "what is
the risk state of an EXISTING portfolio/book?" -- a lifecycle-stage
mismatch, not evidence that the opportunity is unsafe.

STEP 1 AUDIT SUMMARY (see docs/PHASE_20_17_1_RISK_CONTEXT_ADAPTER_REPORT.md
for the full audit):

- `bujji.trading_brain.risk_governor.capital_safety_governor` (D.1) --
  A) reusable directly. The one real governor stage that is genuinely
  pre-trade-compatible: it evaluates a PROPOSED trade's effect on
  capital, not an existing book. Reused via the same real function
  Phase 20.17 (`bujji.risk_governor_bridge`) already calls; not
  reimplemented.
- `bujji.trading_brain.risk_governor.portfolio_risk_aggregator` (D.2) --
  C) wrong lifecycle stage for THIS phase. Phase 20.17 already proved
  `aggregate_portfolio_risk()` marks a flat/zero-position book
  `RISK_INVALID` -- it requires a real `MarginSnapshot`, itself
  requiring a real broker margin query against a real position. This
  phase does NOT call it; it names the resulting gap directly
  (`NOT_READY_FOR_CAPITAL_APPROVAL`) instead of forcing an
  unanswerable question.
- `bujji.trading_brain.risk_governor.strategy_risk_adapter` /
  `governor_context_builder` (Gate E.1/E.2) -- B) reusable PATTERN
  only. Both real, already-tested adapters, but both target a
  DIFFERENT domain: a real `TradeConstructionAssessment` from
  `msi_trade_construction` (an already-built option-leg proposal with
  strikes/contracts), not Cycle 1's own evidence-based
  `FinalDecision`/`AllocationAssessment`. Re-reading
  `governor_context_builder.py`'s own Part 1 findings confirmed the
  SAME structural fact this phase relies on: with zero active position
  groups, its own consistency check (`if active_ids and margin_snapshot
  is None: fail`) is silently skipped, meaning even that builder cannot
  produce a genuinely valid D.2 context for a flat book either -- this
  is a real, codebase-wide gap, not specific to Cycle 1.
- `bujji.risk_governor_bridge` (Phase 20.17) -- A) reusable directly
  for its notional-probe constants (`NOTIONAL_PROBE_MARGIN`,
  `NOTIONAL_PROBE_MAX_LOSS`) -- imported, never redefined, so both
  phases' probes stay numerically identical by construction.
- `bujji.decision_orchestration.FinalDecision` (Phase 20.10),
  `bujji.capital_intelligence.AllocationAssessment` (Phase 20.8),
  `bujji.opportunity_ranking.RankingResult` (Phase 20.7),
  `bujji.opportunity_portfolio.PortfolioDecision` (Phase 20.9) -- A)
  reusable directly as this adapter's inputs. Never recomputed, never
  modified.

This package NEVER modifies the Risk Governor or Capital Management
Engine, NEVER creates a fake `MarginSnapshot` or fake position, NEVER
bypasses a real risk check, and NEVER approves a trade. It only
translates a Cycle-1 opportunity into a `RiskContextRequest` and
reports what the real, unmodified D.1 governor says about it -- with
an honest `NOT_READY_FOR_CAPITAL_APPROVAL`/`UNAVAILABLE_RISK_CONTEXT`
whenever the question asked cannot yet be honestly answered.
"""
from .adapter import build_risk_context_request, evaluate_risk_context
from .explain import explain_risk_context
from .models import (
    ALL_STATUSES, RiskContextAssessment, RiskContextRequest,
    STATUS_NOT_EVALUATED, STATUS_NOT_READY_FOR_CAPITAL_APPROVAL, STATUS_READY_FOR_REVIEW,
    STATUS_RESTRICTED, STATUS_UNAVAILABLE_RISK_CONTEXT,
)

__all__ = [
    "build_risk_context_request", "evaluate_risk_context", "explain_risk_context",
    "RiskContextRequest", "RiskContextAssessment", "ALL_STATUSES",
    "STATUS_READY_FOR_REVIEW", "STATUS_NOT_READY_FOR_CAPITAL_APPROVAL",
    "STATUS_RESTRICTED", "STATUS_UNAVAILABLE_RISK_CONTEXT", "STATUS_NOT_EVALUATED",
]
