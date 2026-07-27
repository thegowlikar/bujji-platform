"""Market Episode Engine runner — public composition entrypoints.

Both entrypoints below delegate to `engine.process_event` /
`engine.advance_time` for every state change -- there is exactly one
grouping/lifecycle implementation in this package. Replay/live parity
(Deliverable 9) therefore holds *by construction*, not by coincidence:
batch mode simply calls the same two functions the incremental/live
mode calls, once per event (and once per time-advance tick), threading
the same `episodes` tuple forward. See
`tests/test_market_episode_engine.py::test_replay_live_parity` for the
proof.

Deliverable 7's publication interface: broker-neutral
`typing.Protocol` stubs only, matching Series 74/75's precedent for an
un-connected future interface. This sprint connects to none of MSI,
Observatory, Replay, or Qualification.
"""
from __future__ import annotations

from typing import List, Optional, Protocol, Sequence, Tuple, Union, runtime_checkable

from bujji.live_market_events.models import MarketEvent

from . import engine
from .models import Episode


# ---------------------------------------------------------------------------
# Deliverable 7 — publication interfaces (future; stubs only, no
# downstream coupling).
# ---------------------------------------------------------------------------
@runtime_checkable
class EpisodePublisher(Protocol):
    def publish(self, episode: Episode) -> None: ...


class InMemoryEpisodePublisher:
    """No-op-safe, in-memory-collecting default publisher — for tests/
    demonstration only, never a stand-in for a real MSI/Observatory/
    Replay/Qualification consumer."""

    def __init__(self) -> None:
        self.published: List[Episode] = []

    def publish(self, episode: Episode) -> None:
        self.published.append(episode)


@runtime_checkable
class MSIEpisodeConsumer(Protocol):
    """Stub for MSI's future consumption of closed/active Episodes.
    Never implemented or wired in this sprint."""

    def on_episode_snapshot(self, episode: Episode) -> None: ...


@runtime_checkable
class ObservatoryEpisodeConsumer(Protocol):
    def on_episode_snapshot(self, episode: Episode) -> None: ...


@runtime_checkable
class ReplayEpisodeConsumer(Protocol):
    def on_episode_snapshot(self, episode: Episode) -> None: ...


@runtime_checkable
class QualificationEpisodeConsumer(Protocol):
    def on_episode_snapshot(self, episode: Episode) -> None: ...


# A tick in a batch walk is either a real MarketEvent or a bare
# timestamp string representing a pure time-advance point (no new
# event). Both a batch walk and the incremental stream accept either.
Tick = Union[MarketEvent, str]


def _record_and_publish(episode: Episode, *, journal=None, publisher: Optional[EpisodePublisher] = None) -> None:
    if journal is not None:
        journal.record_episode(episode)
    if publisher is not None:
        publisher.publish(episode)


def _record_all_new_or_changed(
    before: Tuple[Episode, ...],
    after: Tuple[Episode, ...],
    *,
    journal=None,
    publisher: Optional[EpisodePublisher] = None,
) -> None:
    before_by_id = {e.episode_id: e for e in before}
    for episode in after:
        prior = before_by_id.get(episode.episode_id)
        if prior is None or prior != episode:
            _record_and_publish(episode, journal=journal, publisher=publisher)


# ---------------------------------------------------------------------------
# Batch / replay mode — walk a full historical event sequence, in
# order, interleaving explicit time-advance ticks.
# ---------------------------------------------------------------------------
def generate_episodes_for_events(
    events: Tuple[MarketEvent, ...],
    *,
    journal=None,
    publisher: Optional[EpisodePublisher] = None,
) -> Tuple[Episode, ...]:
    """Walk `events` in stored order, generating/growing/transitioning
    Episodes for each one via `engine.process_event`, then applying
    `engine.advance_time` up to that event's own timestamp before
    processing it (so silence-based transitions fire exactly where a
    live stream would have observed them). Returns the final tuple of
    Episode snapshots (open + closed)."""
    episodes: Tuple[Episode, ...] = ()
    for event in events:
        before = episodes
        episodes = engine.advance_time(episodes, event.timestamp, detection_context="REPLAY")
        episodes = engine.process_event(episodes, event, detection_context="REPLAY")
        _record_all_new_or_changed(before, episodes, journal=journal, publisher=publisher)
    return episodes


# ---------------------------------------------------------------------------
# Live / incremental mode — one event (or one time-advance tick) at a
# time, matching how Series 75's stream would feed this engine.
# ---------------------------------------------------------------------------
class LiveMarketEpisodeStream:
    """Stateful convenience wrapper carrying the open-`Episode` tuple
    forward between calls so callers do not have to. Delegates every
    state change to `engine.process_event`/`engine.advance_time` --
    the SAME functions `generate_episodes_for_events` calls -- so
    incremental and batch processing of an identical event sequence
    produce identical Episode snapshots (see the parity test)."""

    def __init__(self, *, journal=None, publisher: Optional[EpisodePublisher] = None) -> None:
        self._episodes: Tuple[Episode, ...] = ()
        self.journal = journal
        self.publisher = publisher

    @property
    def episodes(self) -> Tuple[Episode, ...]:
        return self._episodes

    def handle_event(self, event: MarketEvent) -> Tuple[Episode, ...]:
        before = self._episodes
        self._episodes = engine.advance_time(self._episodes, event.timestamp, detection_context="LIVE")
        self._episodes = engine.process_event(self._episodes, event, detection_context="LIVE")
        _record_all_new_or_changed(before, self._episodes, journal=self.journal, publisher=self.publisher)
        return self._episodes

    def handle_time_advance(self, current_time: str) -> Tuple[Episode, ...]:
        before = self._episodes
        self._episodes = engine.advance_time(self._episodes, current_time, detection_context="LIVE")
        _record_all_new_or_changed(before, self._episodes, journal=self.journal, publisher=self.publisher)
        return self._episodes
