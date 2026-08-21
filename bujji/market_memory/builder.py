"""Phase 20.15 -- the Shadow Result -> Market Memory bridge. Composes
ONLY already-real fields from Phase 20.11's `DecisionObservation` --
never recomputes `market_regime`/`volatility_state`/`risk_state`
(those come from `DecisionObservation.market_state`, itself Phase
20.1's own `MarketState.to_dict()`, untouched) and never recomputes
`decision_state`/`confidence`/`allocation_class` (Phase 20.10/20.5/
20.8's own values, carried through unmodified).
"""
from __future__ import annotations

from typing import Optional

from bujji.shadow_decision_runtime import DecisionObservation

from .models import DecisionMemoryRecord, MarketMemoryRecord, OutcomeMemoryRecord, STATUS_KNOWN, STATUS_NOT_YET_OBSERVED, memory_id_for


def build_market_memory_record(observation: DecisionObservation) -> MarketMemoryRecord:
    ms = observation.market_state
    return MarketMemoryRecord(
        memory_id=memory_id_for(observation.observation_id),
        as_of_time=observation.timestamp,
        market_regime=ms.get("market_regime", "UNKNOWN"),
        volatility_state=ms.get("volatility_state", "UNKNOWN"),
        risk_state=ms.get("risk_state", "UNKNOWN"),
        data_quality=observation.data_quality,
        evidence=tuple(ms.get("evidence", ())),
    )


def build_decision_memory_record(observation: DecisionObservation) -> DecisionMemoryRecord:
    return DecisionMemoryRecord(
        memory_id=memory_id_for(observation.observation_id),
        as_of_time=observation.timestamp,
        candidate_strategy=observation.candidate_strategy,
        decision_state=observation.decision_state,
        confidence=observation.confidence,
        priority_score=observation.priority_score,
        allocation_class=observation.allocation_class,
        reason_codes=observation.reason_codes,
        uncertainty=observation.uncertainty,
    )


def build_pending_outcome_memory_record(memory_id: str) -> OutcomeMemoryRecord:
    """The honest default at write-time: no later cycle has happened
    yet, so nothing about "what happened afterwards" is known."""
    return OutcomeMemoryRecord(memory_id=memory_id, status=STATUS_NOT_YET_OBSERVED)


def build_known_outcome_memory_record(
    original: MarketMemoryRecord, later_market: MarketMemoryRecord, later_decision: Optional[DecisionMemoryRecord],
) -> OutcomeMemoryRecord:
    """A real, later `MarketMemoryRecord` (and optionally a matching
    later `DecisionMemoryRecord` for the same candidate strategy) is
    factually compared against `original` -- never a prediction, never
    a judgement of whether the original reasoning was "right.\""""
    return OutcomeMemoryRecord(
        memory_id=original.memory_id, status=STATUS_KNOWN, observed_at=later_market.as_of_time,
        regime_after=later_market.market_regime, volatility_state_after=later_market.volatility_state,
        decision_state_after=later_decision.decision_state if later_decision else None,
        regime_unchanged=(later_market.market_regime == original.market_regime),
    )
