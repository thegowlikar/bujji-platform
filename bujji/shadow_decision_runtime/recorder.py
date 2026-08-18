"""Phase 20.11 -- recording. In-memory, append-only accumulation of a
session's `DecisionObservation`s and the summary derived from them. No
new persistence mechanism -- if a durable store is ever needed, that is
a future caller's concern outside this phase's own minimal scope
(mirroring the charter's "minimal only, no framework" instruction).
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from bujji.decision_orchestration import BLOCKED, NO_OPPORTUNITY, WATCH

from .models import DecisionObservation, SessionDecisionSummary

# Mirrors bujji.epistemics.uncertainty's own internal confidence
# ranking (that module's `_RANK` is private) -- a deliberate, disclosed
# mirror rather than an import of a private symbol, the same pattern
# Phase 20.8's `demote_allocation` already established for its own
# differently-shaped vocabulary.
_CONFIDENCE_RANK = {"NONE": 0, "LOW": 1, "MODERATE": 2, "HIGH": 3}


def record_decision(observation: DecisionObservation) -> DecisionObservation:
    """The single required recording entry point. Validates (via
    `DecisionObservation.__post_init__`, already run at construction)
    and returns the observation unchanged -- every write in this
    package passes through here, matching the phase spec's own named
    export."""
    if not isinstance(observation, DecisionObservation):
        raise TypeError(f"expected DecisionObservation, got {type(observation)!r}")
    return observation


class ShadowDecisionLog:
    """One session's append-only collection of `DecisionObservation`s.
    No update, no overwrite, no delete -- `record()` is the only
    mutator."""

    def __init__(self) -> None:
        self._observations: List[DecisionObservation] = []

    def record(self, observation: DecisionObservation) -> DecisionObservation:
        validated = record_decision(observation)
        self._observations.append(validated)
        return validated

    @property
    def observations(self) -> Tuple[DecisionObservation, ...]:
        return tuple(self._observations)

    def summarize(self, session_date: str) -> SessionDecisionSummary:
        return build_session_summary(session_date, self.observations)


def build_session_summary(
    session_date: str, observations: Sequence[DecisionObservation],
) -> SessionDecisionSummary:
    """Counts and distributions derived DIRECTLY from `observations` --
    never a re-judgement of any individual one."""
    decision_distribution: Dict[str, int] = dict(Counter(o.decision_state for o in observations))
    candidate_names = {o.candidate_strategy for o in observations if o.candidate_strategy is not None}

    confident = [o for o in observations if o.confidence is not None]
    highest_confidence_decision: Optional[str] = None
    if confident:
        best = max(confident, key=lambda o: _CONFIDENCE_RANK.get(o.confidence, -1))
        highest_confidence_decision = best.candidate_strategy

    uncertainty_summary: Dict[str, int] = dict(Counter(u for o in observations for u in o.uncertainty))

    return SessionDecisionSummary(
        session_date=session_date,
        number_of_cycles=len(observations),
        candidate_count=len(candidate_names),
        decision_distribution=decision_distribution,
        highest_confidence_decision=highest_confidence_decision,
        blocked_count=decision_distribution.get(BLOCKED, 0),
        watch_count=decision_distribution.get(WATCH, 0),
        no_opportunity_count=decision_distribution.get(NO_OPPORTUNITY, 0),
        uncertainty_summary=uncertainty_summary,
    )
