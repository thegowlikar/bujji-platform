"""Multi-Domain Consensus Intelligence runner — public composition
entrypoints.

Both batch and incremental entrypoints below delegate to the SAME
`engine.compute_consensus_with_explanation` pure function for every
assessment -- there is exactly one reasoning implementation in this
package. Replay/live parity therefore holds by construction, mirroring
`bujji.msi_market_structure.runner`'s exact precedent.
"""
from __future__ import annotations

from typing import List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from . import config as _config
from . import engine
from .engine import DomainAssessmentView
from .models import ConsensusAssessment


@runtime_checkable
class ConsensusPublisher(Protocol):
    def publish(self, assessment: ConsensusAssessment) -> None: ...


class InMemoryConsensusPublisher:
    """No-op-safe, in-memory-collecting default publisher — for tests/
    demonstration only, never a stand-in for a real downstream
    consumer."""

    def __init__(self) -> None:
        self.published: List[ConsensusAssessment] = []

    def publish(self, assessment: ConsensusAssessment) -> None:
        self.published.append(assessment)


def _record_and_publish(assessment: ConsensusAssessment, *, journal=None, publisher: Optional[ConsensusPublisher] = None) -> None:
    if journal is not None:
        journal.record_assessment(assessment)
    if publisher is not None:
        publisher.publish(assessment)


def compute_consensus_for_cycles(
    domain_view_cycles: Sequence[Sequence[DomainAssessmentView]],
    *,
    timestamps: Sequence[str],
    expected_domains: Sequence[str] = _config.DEFAULT_EXPECTED_DOMAINS,
    journal=None,
    publisher: Optional[ConsensusPublisher] = None,
) -> Tuple[ConsensusAssessment, ...]:
    """`domain_view_cycles` is an ordered sequence of domain-view-tuple
    states (one per cycle). Produces one ConsensusAssessment per cycle,
    threading `previous_assessment` forward so `Explanation.what_changed`
    is populated correctly."""
    assessments: List[ConsensusAssessment] = []
    previous: Optional[ConsensusAssessment] = None
    provenance = "msi_consensus.runner.compute_consensus_for_cycles[REPLAY]"
    for views, timestamp in zip(domain_view_cycles, timestamps):
        assessment = engine.compute_consensus_with_explanation(
            tuple(views), previous, timestamp=timestamp, expected_domains=expected_domains,
            provenance=provenance,
        )
        assessments.append(assessment)
        _record_and_publish(assessment, journal=journal, publisher=publisher)
        previous = assessment
    return tuple(assessments)


class ConsensusStream:
    def __init__(
        self,
        *,
        expected_domains: Sequence[str] = _config.DEFAULT_EXPECTED_DOMAINS,
        journal=None,
        publisher: Optional[ConsensusPublisher] = None,
    ) -> None:
        self._previous: Optional[ConsensusAssessment] = None
        self._expected_domains = expected_domains
        self.journal = journal
        self.publisher = publisher

    @property
    def latest(self) -> Optional[ConsensusAssessment]:
        return self._previous

    def handle_domain_views(
        self,
        domain_views: Sequence[DomainAssessmentView],
        *,
        timestamp: str,
    ) -> ConsensusAssessment:
        assessment = engine.compute_consensus_with_explanation(
            tuple(domain_views), self._previous, timestamp=timestamp,
            expected_domains=self._expected_domains,
            provenance="msi_consensus.runner.ConsensusStream[LIVE]",
        )
        _record_and_publish(assessment, journal=self.journal, publisher=self.publisher)
        self._previous = assessment
        return assessment
