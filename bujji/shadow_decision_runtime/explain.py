"""Phase 20.11 -- explainability. Must answer, from a STORED
observation alone (never from the live objects that produced it):
what did Bujji decide, under what market conditions, with what
evidence, and what remains unknown. Built entirely from
`DecisionObservation`'s own already-real fields -- proves reasons
survive storage.
"""
from __future__ import annotations

from .models import DecisionObservation


def explain_observation(observation: DecisionObservation) -> str:
    ms = observation.market_state
    lines = [
        f"Observation: {observation.observation_id}",
        f"Timestamp: {observation.timestamp}",
        f"Decision: {observation.decision_state}",
        f"Candidate: {observation.candidate_strategy or 'UNKNOWN'}",
        f"Market: regime={ms.get('market_regime')}, risk={ms.get('risk_state')}, "
        f"volatility={ms.get('volatility_state')}, data_quality={observation.data_quality}",
    ]
    if observation.priority_score is not None:
        lines.append(f"Priority score: {observation.priority_score:.1f}")
    if observation.allocation_class:
        lines.append(f"Allocation: {observation.allocation_class}")
    if observation.confidence:
        lines.append(f"Confidence: {observation.confidence}")
    if observation.reason_codes:
        lines.append("Reasons:")
        for r in observation.reason_codes:
            lines.append(f"  - {r}")
    if observation.uncertainty:
        lines.append("Unknown:")
        for u in observation.uncertainty:
            lines.append(f"  ? {u}")
    return "\n".join(lines)
