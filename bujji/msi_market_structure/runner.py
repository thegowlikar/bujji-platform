"""Market Structure Intelligence runner — public composition entrypoints.

Both batch and incremental entrypoints below delegate to the SAME
`engine.assess_market_structure` pure function for every assessment —
there is exactly one reasoning implementation in this package. Replay/
live parity therefore holds by construction, mirroring
`bujji.msi_price_structure.runner`'s exact precedent.

Deliberately, there is NO time-advance entrypoint anywhere in this
module — a new assessment is only ever produced when a caller supplies
a (possibly grown) episode/event set. This is the structural
enforcement of "transitions must be evidence-driven, never
time-driven."
"""
from __future__ import annotations

from typing import List, Optional, Protocol, Tuple, runtime_checkable

from bujji.live_market_events.models import MarketEvent
from bujji.market_episode.models import Episode

from . import engine
from .models import MarketStructureAssessment


@runtime_checkable
class MarketStructurePublisher(Protocol):
    def publish(self, assessment: MarketStructureAssessment) -> None: ...


class InMemoryMarketStructurePublisher:
    """No-op-safe, in-memory-collecting default publisher — for tests/
    demonstration only, never a stand-in for a real Regime Intelligence/
    Observatory/Replay/Qualification consumer."""

    def __init__(self) -> None:
        self.published: List[MarketStructureAssessment] = []

    def publish(self, assessment: MarketStructureAssessment) -> None:
        self.published.append(assessment)


def _record_and_publish(assessment: MarketStructureAssessment, *, journal=None, publisher: Optional[MarketStructurePublisher] = None) -> None:
    if journal is not None:
        journal.record_assessment(assessment)
    if publisher is not None:
        publisher.publish(assessment)


def assess_market_structure_for_episodes(
    episode_snapshots: Tuple[Tuple[Episode, ...], ...],
    events: Tuple[MarketEvent, ...],
    *,
    timestamps: Tuple[str, ...],
    detection_context: str = "REPLAY",
    journal=None,
    publisher: Optional[MarketStructurePublisher] = None,
) -> Tuple[MarketStructureAssessment, ...]:
    """`episode_snapshots` is an ordered sequence of episode-tuple
    states. Produces one MarketStructureAssessment per snapshot,
    threading `previous_assessment` forward so `Explanation.what_changed`
    is populated correctly."""
    assessments: List[MarketStructureAssessment] = []
    previous: Optional[MarketStructureAssessment] = None
    provenance = f"msi_market_structure.runner.assess_market_structure_for_episodes[{detection_context}]"
    for episodes, timestamp in zip(episode_snapshots, timestamps):
        assessment = engine.assess_market_structure(
            episodes, events, timestamp=timestamp, previous_assessment=previous, provenance=provenance,
        )
        assessments.append(assessment)
        _record_and_publish(assessment, journal=journal, publisher=publisher)
        previous = assessment
    return tuple(assessments)


class MarketStructureStream:
    def __init__(self, *, journal=None, publisher: Optional[MarketStructurePublisher] = None) -> None:
        self._previous: Optional[MarketStructureAssessment] = None
        self.journal = journal
        self.publisher = publisher

    @property
    def latest(self) -> Optional[MarketStructureAssessment]:
        return self._previous

    def handle_episodes(
        self,
        episodes: Tuple[Episode, ...],
        events: Tuple[MarketEvent, ...],
        *,
        timestamp: str,
    ) -> MarketStructureAssessment:
        assessment = engine.assess_market_structure(
            episodes, events, timestamp=timestamp, previous_assessment=self._previous,
            provenance="msi_market_structure.runner.MarketStructureStream[LIVE]",
        )
        _record_and_publish(assessment, journal=self.journal, publisher=self.publisher)
        self._previous = assessment
        return assessment
