"""Price Structure Intelligence runner — public composition entrypoints.

Both batch and incremental entrypoints below delegate to the SAME
`engine.assess_price_structure` pure function for every assessment —
there is exactly one reasoning implementation in this package. Replay/
live parity therefore holds by construction, mirroring Series 75/76/
77's exact precedent — see
`tests/test_msi_price_structure_intelligence.py::test_replay_live_parity`.

Deliberately, there is NO time-advance entrypoint anywhere in this
module (unlike `bujji.market_episode.runner`'s `advance_time`/
`handle_time_advance`) — a new assessment is only ever produced when a
caller supplies a (possibly grown) episode/event set. This is the
structural enforcement of Deliverable 4's "transitions must be
evidence-driven, never time-driven" requirement for `structure_state`.
"""
from __future__ import annotations

from typing import List, Optional, Protocol, Tuple, runtime_checkable

from bujji.live_market_events.models import MarketEvent
from bujji.market_episode.models import Episode

from . import engine
from .models import PriceStructureAssessment


@runtime_checkable
class PriceStructurePublisher(Protocol):
    def publish(self, assessment: PriceStructureAssessment) -> None: ...


class InMemoryPriceStructurePublisher:
    """No-op-safe, in-memory-collecting default publisher — for tests/
    demonstration only, never a stand-in for a real Regime Intelligence/
    Observatory/Replay/Qualification consumer."""

    def __init__(self) -> None:
        self.published: List[PriceStructureAssessment] = []

    def publish(self, assessment: PriceStructureAssessment) -> None:
        self.published.append(assessment)


def _record_and_publish(assessment: PriceStructureAssessment, *, journal=None, publisher: Optional[PriceStructurePublisher] = None) -> None:
    if journal is not None:
        journal.record_assessment(assessment)
    if publisher is not None:
        publisher.publish(assessment)


# ---------------------------------------------------------------------------
# Batch / replay mode — walk a full historical (episodes, events) walk,
# producing one assessment per step (a step = the episode/event set as
# it stood at that point in the walk). Returns every produced
# assessment, in order.
# ---------------------------------------------------------------------------
def assess_price_structure_for_episodes(
    episode_snapshots: Tuple[Tuple[Episode, ...], ...],
    events: Tuple[MarketEvent, ...],
    *,
    timestamps: Tuple[str, ...],
    detection_context: str = "REPLAY",
    journal=None,
    publisher: Optional[PriceStructurePublisher] = None,
) -> Tuple[PriceStructureAssessment, ...]:
    """`episode_snapshots` is an ordered sequence of episode-tuple
    states (e.g. the open-episode set after each new event was
    processed by Series 76's engine). Produces one
    PriceStructureAssessment per snapshot, threading `previous_assessment`
    forward so `Explanation.what_changed` is populated correctly."""
    assessments: List[PriceStructureAssessment] = []
    previous: Optional[PriceStructureAssessment] = None
    provenance = f"msi_price_structure.runner.assess_price_structure_for_episodes[{detection_context}]"
    for episodes, timestamp in zip(episode_snapshots, timestamps):
        assessment = engine.assess_price_structure(
            episodes, events, timestamp=timestamp, previous_assessment=previous, provenance=provenance,
        )
        assessments.append(assessment)
        _record_and_publish(assessment, journal=journal, publisher=publisher)
        previous = assessment
    return tuple(assessments)


# ---------------------------------------------------------------------------
# Live / incremental mode — one (episodes, events, timestamp) step at a
# time, carrying the previous assessment forward between calls.
# Delegates to the SAME `engine.assess_price_structure` function the
# batch entrypoint calls.
# ---------------------------------------------------------------------------
class PriceStructureStream:
    def __init__(self, *, journal=None, publisher: Optional[PriceStructurePublisher] = None) -> None:
        self._previous: Optional[PriceStructureAssessment] = None
        self.journal = journal
        self.publisher = publisher

    @property
    def latest(self) -> Optional[PriceStructureAssessment]:
        return self._previous

    def handle_episodes(
        self,
        episodes: Tuple[Episode, ...],
        events: Tuple[MarketEvent, ...],
        *,
        timestamp: str,
    ) -> PriceStructureAssessment:
        assessment = engine.assess_price_structure(
            episodes, events, timestamp=timestamp, previous_assessment=self._previous,
            provenance="msi_price_structure.runner.PriceStructureStream[LIVE]",
        )
        _record_and_publish(assessment, journal=self.journal, publisher=self.publisher)
        self._previous = assessment
        return assessment
