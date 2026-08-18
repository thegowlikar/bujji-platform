"""Phase 20.21 -- deterministic learning classification. A fixed,
documented if/elif rule table, mirroring `bujji.trading_brain.
risk_governor.adaptive_risk_recommendation`'s own established
discipline (Gate D.5, Part 5): "NOT machine learning, NOT a
probability model, NOT reinforcement learning ... every threshold
used ... a named module-level constant." No hidden scoring anywhere in
this module.

RULE 1 (never violated): this function never reads or writes
`evidence_score`/`qualification_score`/`ranking_score`/
`allocation_score`/`risk_score` -- `ShadowResultRecord` itself does
not carry any of them (Phase 20.20's own established boundary), so
there is nothing here to accidentally touch.

RULE 2 (never violated): the return value is a `LearningUpdateRecord`
-- a memory-evidence object. This function never emits a decision,
never emits a recommendation to trade, and is never called from any
live decision path (mirrors `bujji.outcome_memory.query`'s own
explicit "Decision -> Outcome -> Memory, never Memory -> Decision"
disclosure).

RULE 3 (never violated): the sole input is one already-completed
`ShadowResultRecord` -- no market data, no future cycle, no lookahead
of any kind is read here.
"""
from __future__ import annotations

from typing import Optional

from bujji.decision_orchestration import BLOCKED, INSUFFICIENT_INTELLIGENCE, NO_OPPORTUNITY
from bujji.risk_context_adapter import (
    STATUS_NOT_EVALUATED, STATUS_RESTRICTED, STATUS_UNAVAILABLE_RISK_CONTEXT,
)
from bujji.execution_intelligence import STATUS_FILLED, STATUS_PARTIAL, STATUS_REJECTED
from bujji.broker_boundary import INCONSISTENT, MISSING_RESPONSE
from bujji.shadow_result import ShadowResultRecord
from bujji.decision_orchestration import EXECUTABLE_CANDIDATE

from .models import (
    CONFIRMED_PATTERN, CONFLICTING_SIGNAL, FAILED_PATTERN, INSUFFICIENT_RESULT, UNAVAILABLE_DATA,
    LearningUpdateRecord, update_id_for,
)

_NO_DECISION_STATES = (NO_OPPORTUNITY, BLOCKED, INSUFFICIENT_INTELLIGENCE)
_NO_EVALUABLE_RISK_STATUSES = (None, STATUS_NOT_EVALUATED, STATUS_UNAVAILABLE_RISK_CONTEXT, STATUS_RESTRICTED)
_FILLED_STATUSES = (STATUS_FILLED, STATUS_PARTIAL)


def _classify(shadow_result: ShadowResultRecord) -> str:
    # Step 1: no real decision worth learning from.
    if shadow_result.decision_state in _NO_DECISION_STATES:
        return INSUFFICIENT_RESULT

    # Step 2: risk was never cleared (unavailable, un-evaluated, or a
    # real restriction) -- no execution outcome could ever have been
    # produced, so there is nothing to confirm or fail.
    if shadow_result.risk_context_status in _NO_EVALUABLE_RISK_STATUSES:
        return INSUFFICIENT_RESULT

    # Step 3: risk cleared, but the pipeline never reached
    # reconciliation at all -- a genuine data gap, not a rejection.
    if shadow_result.reconciliation_consistency is None:
        return UNAVAILABLE_DATA

    # Step 4: a real reconciliation mismatch -- the result cannot be
    # trusted either way.
    if shadow_result.reconciliation_consistency in (INCONSISTENT, MISSING_RESPONSE):
        return CONFLICTING_SIGNAL

    # Step 5: reconciliation is CONSISTENT -- the real, trustworthy case.
    if shadow_result.execution_result_status is None:
        # Simulation was correctly never run (e.g. RESTRICTED) -- no
        # fill outcome exists to learn from.
        return INSUFFICIENT_RESULT
    if shadow_result.execution_result_status == STATUS_REJECTED:
        return FAILED_PATTERN
    if shadow_result.execution_result_status in _FILLED_STATUSES and shadow_result.decision_state == EXECUTABLE_CANDIDATE:
        return CONFIRMED_PATTERN

    # A real fill exists, but the decision was only WATCH (not a real
    # candidate the Decision Brain itself was confident enough to
    # call executable) -- conservative default, disclosed rather than
    # silently upgraded to a confirmed pattern.
    return INSUFFICIENT_RESULT


def evaluate_shadow_result_for_learning(
    shadow_result: ShadowResultRecord, *, market_context_signature: Optional[str] = None, created_at: str,
) -> LearningUpdateRecord:
    """`market_context_signature` is an OPTIONAL, caller-supplied
    enrichment: `ShadowResultRecord` itself (Phase 20.20's own
    established schema) does not carry market regime -- honestly
    `None` unless the caller supplies it from the same real
    `AllocationAssessment`/`MarketEnvironment` used earlier in the
    same cycle. Never fabricated when absent."""
    classification = _classify(shadow_result)
    return LearningUpdateRecord(
        update_id=update_id_for(shadow_result.record_id),
        source_shadow_result_id=shadow_result.record_id,
        strategy_family=shadow_result.strategy_name,
        market_context_signature=market_context_signature,
        decision_state=shadow_result.decision_state,
        risk_context_state=shadow_result.risk_context_status,
        execution_outcome=shadow_result.execution_result_status,
        reconciliation_state=shadow_result.reconciliation_consistency,
        learning_classification=classification,
        created_at=created_at,
    )
