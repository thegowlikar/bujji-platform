"""Phase 20.21 -- Learning Update Layer: Shadow Result -> Market
Memory Feedback.

Closes the final arrow of the Interface Map's own pipeline:
Market -> Observation -> Intelligence -> Decision -> Risk -> Execution
Simulation -> Shadow Result -> LEARNING UPDATE -> Market Memory ->
Improved Intelligence.

STEP 1 AUDIT SUMMARY (see docs/PHASE_20_21_LEARNING_UPDATE_REPORT.md
for the full audit):

- `bujji.outcome_memory` (Phase 15N) -- C) wrong domain, B) pattern
  only for its own explicit disclosure. Real P&L/position outcome
  memory, requires real positions Cycle 1 does not have. Its own
  module docstring states the exact discipline this phase follows:
  "Decision -> Outcome -> Memory ... NEVER Decision -> Outcome ->
  Memory -> Decision (that feedback loop is an explicit future phase
  with a much higher trust bar)" -- `evaluator.py` mirrors this
  exactly: it is never called from any live decision path.
- `bujji.trading_brain.risk_governor.adaptive_risk_recommendation`
  (Gate D.5, Part 5) -- B) reusable pattern only. A real, deterministic
  "fixed if/elif rule table, every threshold a named constant, the
  rule that fired always recorded" classifier over real risk-memory
  statistics. This phase's own `evaluator._classify()` mirrors that
  exact discipline over `ShadowResultRecord` fields instead. Not
  imported -- a different domain (real position risk, not paper
  execution learning).
- `bujji.market_memory.models.OutcomeMemoryRecord` (Phase 20.15) -- C)
  wrong schema for this phase's data. Genuinely inspected and found to
  encode MARKET-REGIME continuity (`regime_after`, `volatility_state_
  after`, `regime_unchanged`) sourced from `DecisionObservation`/
  `MarketMemoryRecord` -- it has no field for a paper-execution
  outcome or a reconciliation result, and forcing this phase's data
  into it would misuse an existing schema for something it was never
  designed to hold. Instead, this phase reuses the underlying
  `bujji.state_persistence.store.EventStore` PRIMITIVE directly (the
  same class `market_memory.store` itself is built on) and adds one
  new, additive event type (`LEARNING_UPDATE_RECORDED`) that can
  coexist in the SAME durable file `market_memory` already writes to
  for a session -- satisfying "reuse bujji.market_memory, do not
  create another memory database" literally: one file, one `EventStore`
  class, a fourth coexisting event type alongside `market_memory`'s
  own three. `market_memory`'s own read functions filter by their own
  event_type and are structurally unaffected (verified by a dedicated
  coexistence test).
- `bujji.memory_intelligence` (Phase 20.15.1) -- B) reusable pattern
  only. Its own "fixed rule ladder, one-band confidence adjustment,
  every branch as prominent and tested as every other" discipline
  informed this phase's own rule-ladder shape; its own function
  (`apply_memory_influence`) reads `MemoryContext`/`OutcomeMemoryRecord`
  (Phase 20.15's regime-continuity schema) and is not called here --
  a different input shape entirely.
- `bujji.shadow_result.ShadowResultRecord` (Phase 20.20) -- A) reusable
  directly, this phase's sole input. Never recomputed, never modified.

CRITICAL RULES ENFORCED:
- Rule 1: `evaluator.py` never reads or writes `evidence_score`/
  `qualification_score`/`ranking_score`/`allocation_score`/
  `risk_score` -- `ShadowResultRecord` itself carries none of them.
- Rule 2: `evaluate_shadow_result_for_learning()` returns a
  `LearningUpdateRecord` -- memory evidence only. It is never called
  from, and never returns a value consumed by, any live decision path.
- Rule 3: the sole input is one already-completed `ShadowResultRecord`
  -- no market data, no future cycle, no lookahead of any kind.
"""
from .evaluator import evaluate_shadow_result_for_learning
from .explain import explain_learning_update
from .models import (
    ALL_LEARNING_CLASSIFICATIONS, CONFIRMED_PATTERN, CONFLICTING_SIGNAL, EVENT_LEARNING_UPDATE_RECORDED,
    FAILED_PATTERN, INSUFFICIENT_RESULT, SCHEMA_VERSION, UNAVAILABLE_DATA,
    LearningUpdateRecord, update_id_for,
)
from .store import read_all_learning_updates, record_learning_update

__all__ = [
    "evaluate_shadow_result_for_learning", "record_learning_update", "read_all_learning_updates",
    "explain_learning_update", "LearningUpdateRecord", "update_id_for",
    "ALL_LEARNING_CLASSIFICATIONS", "CONFIRMED_PATTERN", "FAILED_PATTERN", "INSUFFICIENT_RESULT",
    "UNAVAILABLE_DATA", "CONFLICTING_SIGNAL",
    "EVENT_LEARNING_UPDATE_RECORDED", "SCHEMA_VERSION",
]
