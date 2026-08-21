"""Phase 20.11 -- the observer. Reads Phase 20.1's `MarketState` and
Phase 20.10's own already-composed `FinalDecision` (never recomputing
Phase 20.5/20.6/20.7/20.8/20.9/20.10's work) and packages them into one
`DecisionObservation`. No scoring, ranking, qualification, allocation,
conflict, or decision logic lives here -- this module OBSERVES the
chain, it does not extend or re-run it.
"""
from __future__ import annotations

from bujji.decision_orchestration import FinalDecision
from bujji.mic_v0.models import MarketState

from .models import DecisionObservation


def run_shadow_cycle(
    market_state: MarketState,
    final_decision: FinalDecision,
    timestamp: str,
) -> DecisionObservation:
    """One cycle, for one candidate strategy. `final_decision` must
    already be Phase 20.10's own `compose_decision()` output -- this
    function only reads its fields (`strategy_name`, `decision_state`,
    `positive`/`negative`/`unknown`, and the `allocation` it carries
    through) and Phase 20.1's `market_state`, never recalculates
    either."""
    allocation = final_decision.allocation
    priority_score = allocation.priority_score if allocation is not None else None
    allocation_class = allocation.allocation_class if allocation is not None else None
    confidence = (
        allocation.candidate.assessment.strategy_score.confidence
        if allocation is not None else None
    )

    observation_id = f"{final_decision.strategy_name or 'UNKNOWN'}::{timestamp}"

    return DecisionObservation(
        observation_id=observation_id,
        timestamp=timestamp,
        market_state=market_state.to_dict(),
        candidate_strategy=final_decision.strategy_name,
        decision_state=final_decision.decision_state,
        priority_score=priority_score,
        allocation_class=allocation_class,
        confidence=confidence,
        reason_codes=tuple(final_decision.positive) + tuple(final_decision.negative),
        uncertainty=tuple(final_decision.unknown),
        data_quality=market_state.data_quality,
    )
