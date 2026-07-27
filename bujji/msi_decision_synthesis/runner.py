"""MSI Decision Synthesis Engine runner — public composition entrypoints.

Both entrypoints below delegate to `engine.synthesize` /
`engine.build_explanation` for every assessment produced -- there is
exactly one fusion/explanation implementation in this package.
Replay/live parity therefore holds *by construction*, not by
coincidence: batch mode simply calls the same two functions the
incremental/live mode calls, once per cycle, threading the same
`previous_assessment` forward. See
`tests/test_msi_decision_synthesis_engine.py::test_replay_live_parity`
for the proof.
"""
from __future__ import annotations

from typing import List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from . import engine
from .models import DomainSignal, Explanation, MarketOpportunityAssessment


@runtime_checkable
class AssessmentPublisher(Protocol):
    def publish(self, assessment: MarketOpportunityAssessment, explanation: Explanation) -> None: ...


class InMemoryAssessmentPublisher:
    """No-op-safe, in-memory-collecting default publisher — for tests/
    demonstration only, never a stand-in for a real Trading Brain
    consumer."""

    def __init__(self) -> None:
        self.published: List[Tuple[MarketOpportunityAssessment, Explanation]] = []

    def publish(self, assessment: MarketOpportunityAssessment, explanation: Explanation) -> None:
        self.published.append((assessment, explanation))


def _record_and_publish(
    assessment: MarketOpportunityAssessment,
    explanation: Explanation,
    *,
    journal=None,
    publisher: Optional[AssessmentPublisher] = None,
) -> None:
    if journal is not None:
        journal.record_assessment(assessment, explanation)
    if publisher is not None:
        publisher.publish(assessment, explanation)


def synthesize_assessment(
    domain_signals: Tuple[DomainSignal, ...],
    *,
    previous_assessment: Optional[MarketOpportunityAssessment] = None,
    episode_ids: Tuple[str, ...] = (),
    timestamp: str,
    journal=None,
    publisher: Optional[AssessmentPublisher] = None,
) -> Tuple[MarketOpportunityAssessment, Explanation]:
    """Single-cycle synthesis entrypoint."""
    assessment = engine.synthesize(
        domain_signals,
        previous_assessment,
        episode_ids,
        timestamp=timestamp,
    )
    explanation = engine.build_explanation(domain_signals, assessment, previous_assessment)
    _record_and_publish(assessment, explanation, journal=journal, publisher=publisher)
    return assessment, explanation


# ---------------------------------------------------------------------------
# Batch / replay mode — walk a full historical sequence of synthesis
# cycles, in order, threading `previous_assessment` forward exactly as
# the incremental stream does.
# ---------------------------------------------------------------------------
def generate_assessments_for_cycles(
    cycles: Sequence[Tuple[Tuple[DomainSignal, ...], str, Tuple[str, ...]]],
    *,
    journal=None,
    publisher: Optional[AssessmentPublisher] = None,
) -> Tuple[Tuple[MarketOpportunityAssessment, ...], Tuple[Explanation, ...]]:
    """`cycles` is an ordered sequence of (domain_signals, timestamp,
    episode_ids) tuples, one per synthesis cycle. Returns the full
    ordered tuple of assessments and explanations produced."""
    assessments: List[MarketOpportunityAssessment] = []
    explanations: List[Explanation] = []
    previous: Optional[MarketOpportunityAssessment] = None
    for domain_signals, timestamp, episode_ids in cycles:
        assessment, explanation = synthesize_assessment(
            domain_signals,
            previous_assessment=previous,
            episode_ids=episode_ids,
            timestamp=timestamp,
            journal=journal,
            publisher=publisher,
        )
        assessments.append(assessment)
        explanations.append(explanation)
        previous = assessment
    return tuple(assessments), tuple(explanations)


# ---------------------------------------------------------------------------
# Live / incremental mode — one cycle at a time, matching how a future
# real-time MSI fusion loop would feed this engine.
# ---------------------------------------------------------------------------
class DecisionSynthesisStream:
    """Stateful convenience wrapper carrying `previous_assessment`
    forward between calls so callers do not have to. Delegates every
    synthesis to `engine.synthesize`/`engine.build_explanation` -- the
    SAME functions `generate_assessments_for_cycles` calls -- so
    incremental and batch processing of an identical cycle sequence
    produce identical assessments (see the parity test)."""

    def __init__(self, *, journal=None, publisher: Optional[AssessmentPublisher] = None) -> None:
        self._previous: Optional[MarketOpportunityAssessment] = None
        self.journal = journal
        self.publisher = publisher

    @property
    def previous_assessment(self) -> Optional[MarketOpportunityAssessment]:
        return self._previous

    def handle_cycle(
        self,
        domain_signals: Tuple[DomainSignal, ...],
        *,
        timestamp: str,
        episode_ids: Tuple[str, ...] = (),
    ) -> Tuple[MarketOpportunityAssessment, Explanation]:
        assessment, explanation = synthesize_assessment(
            domain_signals,
            previous_assessment=self._previous,
            episode_ids=episode_ids,
            timestamp=timestamp,
            journal=self.journal,
            publisher=self.publisher,
        )
        self._previous = assessment
        return assessment, explanation
