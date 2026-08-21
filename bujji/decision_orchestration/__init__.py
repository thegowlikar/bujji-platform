"""Decision Orchestration Intelligence Layer -- Phase 20.10.

Answers "given everything Bujji knows, what is the final intelligence
decision?" -- NOT execution. No trade signal, order, broker call,
position creation, quantity calculation, stop-loss logic, or entry
timing exists anywhere in this package. `EXECUTABLE_CANDIDATE` means
"worth further downstream review," never "execute."

This layer is a COMPOSER, not a calculator: it duplicates no scoring,
ranking, qualification, regime-detection, or risk logic. Every input
is read from an already-computed upstream object.

NAMING COLLISION CHECK (this phase's own explicit Step 1 instruction),
disclosed prominently: the literal requested path `bujji/decision_
orchestration/` does not collide with anything. A CLOSELY RELATED name
does, though -- `bujji.decision_intelligence` (Phase 19.6, this
engagement) already exists and is ALSO a pure composer ("the one entry
point... composition over already-built objects from every prior
layer"), but composes a completely different, older lineage:
MarketRealitySnapshot -> MarketIntelligenceSnapshot (19.3) ->
DecisionContext (19.4) -> MarketMemoryEntry matches (19.5) ->
DecisionIntelligenceSnapshot -- the pre-MIC-v0, options-domain-adjacent
Phase 19 Intelligence Foundation, never Cycle-1's own MIC v0 / strategy_
research / opportunity_* / capital_intelligence chain. `bujji.
shadow_runtime.intelligence_pipeline_adapter` (Phase 19.10.1) is a
second composer over that SAME older lineage. Neither is reused as
code; both confirm the "pure composition, never recalculate" pattern
this package also follows, independently arrived at twice already in
this codebase.

Reuses, unmodified:
- `bujji.capital_intelligence.AllocationAssessment` (Phase 20.8) --
  the per-strategy input; carries `priority_score`/`rank` (20.7),
  `allocation_class` (20.8), and `strategy_score`/`environment`/
  `qualification_state` (20.5/20.6) transitively.
- `bujji.opportunity_portfolio.PortfolioDecision` (Phase 20.9) -- the
  portfolio-level input; tells this layer which strategies survived
  conflict resolution and why.
- `bujji.mic_v0.models.EVENT_CONTEXT_NOT_AVAILABLE` (Phase 20.1) --
  reused verbatim as the disclosed default for the one MIC input
  (`event_context`) no upstream Cycle-1 layer currently carries; always
  surfaced under "Unknown," never silently dropped.

See docs/PHASE_20_10_DECISION_ORCHESTRATION_REPORT.md.
"""
from .engine import compose_decision
from .explain import explain_decision
from .models import (
    ALL_DECISION_STATES,
    BLOCKED,
    EXECUTABLE_CANDIDATE,
    INSUFFICIENT_INTELLIGENCE,
    NO_OPPORTUNITY,
    WATCH,
    FinalDecision,
)

__all__ = [
    "EXECUTABLE_CANDIDATE", "WATCH", "NO_OPPORTUNITY", "BLOCKED", "INSUFFICIENT_INTELLIGENCE",
    "ALL_DECISION_STATES", "FinalDecision",
    "compose_decision", "explain_decision",
]
