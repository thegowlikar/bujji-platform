"""bujji.memory_intelligence -- Phase 20.15.1 (Bujji OS v1.0 Roadmap,
Cycle-1 lineage).

Closes the Interface Map's Memory -> Strategy Intelligence arrow:
"How should previous similar situations influence our confidence?"
Interpretation only -- storage/retrieval lives in `bujji.market_memory`
(Phase 20.15), consumed here, never reimplemented.

NAMING / COLLISION AUDIT (this phase's own mandatory Step 1): no
`bujji/memory_intelligence/` package existed prior to this phase.

Broad audit of confidence-adjustment/similarity/uncertainty/memory-
query systems found:

  (A, reusable directly) `bujji.epistemics.uncertainty` (Phase 16C) --
  `demote()`, `rank()`, `cap()`, the `HIGH/MODERATE/LOW/NONE` confidence
  vocabulary. Reused unmodified; `evaluator.py`'s own `_promote_one_band`
  is a small, disclosed, symmetric mirror of `demote()` (that module
  exposes no `promote()` -- never needed before this phase), not a
  private-symbol import.
  `bujji.strategy_intelligence.scoring._apply_mic_context()` (Phase
  20.5) -- the ONLY prior confidence-modifying precedent in Cycle 1.
  Its exact discipline (one band, bounded, disclosed, never touches
  `evidence_score`) is mirrored in `evaluator.py`; that function itself
  is never imported or modified -- this package composes around
  `StrategyScore`'s own already-computed `confidence`, never recomputes
  it.

  (A, reusable directly) `bujji.market_memory.{retrieval,models}`
  (Phase 20.15) -- `MemoryContext`, `OutcomeMemoryRecord`,
  `find_similar_conditions_as_of()`. This package is their sole
  consumer; it never reimplements retrieval or persistence.

  (C, wrong domain) `bujji.trading_brain.risk_governor.
  adaptive_risk_governor`/`adaptive_risk_recommendation` -- real
  capital-risk decisioning, MSI/Trading Brain lineage, structurally
  unrelated to Cycle 1's evidence-based confidence model. `bujji.
  market_phenomena`/`market_state_graph` (Phase 19.7/19.8) -- real
  phenomenon/state-graph memory, but the older Phase 19.x lineage
  identity, same disclosed non-reuse precedent every Cycle-1 phase
  since 20.10 has already established. None imported.

HARD BOUNDARY, enforced structurally by `tests/test_memory_intelligence.py`:
this package never modifies `evidence_score`, strategy ranking,
qualification state, or allocation; never creates an opportunity from
nothing; never imports a broker or capital module; never calls
`compose_decision()`/`score_strategy()`/`compose_market_state()`.
"""
from __future__ import annotations

from .evaluator import (
    FAVORABLE_RATIO_THRESHOLD, MIN_KNOWN_OUTCOMES_FOR_INFLUENCE, MIN_SIMILAR_FOR_INFLUENCE,
    UNFAVORABLE_RATIO_THRESHOLD, evaluate_memory_influence,
)
from .explain import explain_memory_influence
from .integration import apply_memory_influence
from .models import (
    ALL_DIRECTIONS, DIRECTION_CONTRADICTED, DIRECTION_SUPPORTED, DIRECTION_UNCHANGED,
    FLAG_CONTRADICTORY_HISTORY, FLAG_INSUFFICIENT_KNOWN_OUTCOMES, FLAG_INSUFFICIENT_SIMILAR, FLAG_NO_MEMORY,
    MemoryInfluenceAssessment,
)

__all__ = [
    "MemoryInfluenceAssessment",
    "ALL_DIRECTIONS", "DIRECTION_SUPPORTED", "DIRECTION_CONTRADICTED", "DIRECTION_UNCHANGED",
    "FLAG_NO_MEMORY", "FLAG_INSUFFICIENT_SIMILAR", "FLAG_INSUFFICIENT_KNOWN_OUTCOMES", "FLAG_CONTRADICTORY_HISTORY",
    "evaluate_memory_influence",
    "MIN_SIMILAR_FOR_INFLUENCE", "MIN_KNOWN_OUTCOMES_FOR_INFLUENCE",
    "FAVORABLE_RATIO_THRESHOLD", "UNFAVORABLE_RATIO_THRESHOLD",
    "apply_memory_influence",
    "explain_memory_influence",
]
