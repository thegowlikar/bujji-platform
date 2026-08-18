"""Capital Allocation Intelligence Layer -- Phase 20.8.

Answers "how much risk budget should this qualified, ranked opportunity
receive?" -- as a RECOMMENDATION CLASS (MAXIMUM/NORMAL/REDUCED/MINIMAL/
NONE), never a quantity, lot count, margin figure, or order. No
trade-sizing arithmetic, and no order/broker vocabulary, exists
anywhere in this package.

Reuses, unmodified:
- `bujji.opportunity_ranking.OpportunityCandidate`/`RankedOpportunity`
  (Phase 20.7) -- `priority_score` is READ, never recomputed.
- `bujji.opportunity_intelligence` qualification states (Phase 20.6),
  transitively.
- `bujji.strategy_intelligence.StrategyScore` (Phase 20.5),
  transitively -- `evidence_score`/`confidence`/`effective_score` are
  read-only inputs here, exactly as in every prior Cycle-1 phase.

Step 1 audit (this phase) found the codebase's real capital/margin
system -- `bujji.trading_brain.risk_governor.*` (Gates B/C/D.1-D.3)
and `bujji.capital.engine.CapitalManagementEngine` -- entirely wrong
domain: both require REAL broker/account/margin state (`CapitalSafety
Snapshot`, `PortfolioRiskSnapshot`, live funds/margin responses) and
both explicitly disclaim exactly what this phase also forbids itself
(`risk_brain`/`capital_brain`'s own docstrings: "no PnL, no Kelly/
Sharpe/Sortino, no Monte Carlo"). Not reused as code. `risk_budget_
governor.py`'s own discipline -- "this module places no trade, calls
no broker, performs no automatic sizing... output is a RECOMMENDATION
downstream systems MAY use later" -- is the exact posture this phase
follows, by design, without importing anything from it.

See docs/PHASE_20_8_CAPITAL_ALLOCATION_INTELLIGENCE_REPORT.md.
"""
from .allocator import assess_risk_allocation, assess_ranking_result
from .explain import explain_allocation
from .models import (
    ALL_ALLOCATION_CLASSES,
    MAXIMUM,
    MINIMAL,
    NONE_ALLOCATION,
    NORMAL,
    REDUCED,
    AllocationAssessment,
    allocation_rank,
    demote_allocation,
)

__all__ = [
    "MAXIMUM", "NORMAL", "REDUCED", "MINIMAL", "NONE_ALLOCATION", "ALL_ALLOCATION_CLASSES",
    "AllocationAssessment", "allocation_rank", "demote_allocation",
    "assess_risk_allocation", "assess_ranking_result", "explain_allocation",
]
