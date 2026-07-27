"""Live Market Event Engine runner — public composition entrypoints.

Both entrypoints below delegate to `engine.compare_observations` for
every pairwise comparison -- there is exactly one comparison
implementation in this package. Replay/live parity (Deliverable 3's
implicit requirement, mirroring Series 74's Observation-layer parity
principle) therefore holds *by construction*, not by coincidence: batch
mode simply calls the same function the incremental/live mode calls,
once per consecutive pair, threading the same `RunningState` forward.
See `tests/test_live_market_events.py::test_replay_live_parity` for the
proof.

Deliverable 6's publication interface: broker-neutral `typing.Protocol`
stubs only, matching Series 74's `runner.py` precedent for an
un-connected future interface. This sprint connects to none of MSI,
Replay, Observatory, or Qualification.
"""
from __future__ import annotations

from typing import List, Optional, Protocol, Tuple, runtime_checkable

from bujji.market_observation.models import Observation, ObservationSeries

from . import engine
from .models import MarketEvent, RunningState


# ---------------------------------------------------------------------------
# Deliverable 6 — publication interfaces (future; stubs only, no
# downstream coupling).
# ---------------------------------------------------------------------------
@runtime_checkable
class MarketEventPublisher(Protocol):
    def publish(self, event: MarketEvent) -> None: ...


class InMemoryMarketEventPublisher:
    """No-op-safe, in-memory-collecting default publisher — for tests/
    demonstration only, never a stand-in for a real MSI/Replay/
    Observatory/Qualification consumer."""

    def __init__(self) -> None:
        self.published: List[MarketEvent] = []

    def publish(self, event: MarketEvent) -> None:
        self.published.append(event)


# ---------------------------------------------------------------------------
# Batch / replay mode — walk a full historical ObservationSeries
# pairwise, in order.
# ---------------------------------------------------------------------------
def generate_events_for_series(
    series: ObservationSeries,
    *,
    journal=None,
    publisher: Optional[MarketEventPublisher] = None,
) -> Tuple[MarketEvent, ...]:
    """Walk `series.observations` pairwise (current, previous), in
    stored order, generating and (optionally) journaling/publishing
    MarketEvents for each pair. Session extremes state (RunningState)
    is threaded forward across the whole walk -- one running fact per
    series, never re-derived by scanning ahead."""
    all_events: List[MarketEvent] = []
    previous: Optional[Observation] = None
    running_state: Optional[RunningState] = None

    for current in series.observations:
        events, running_state = engine.compare_observations(
            current, previous, running_state, detection_context="REPLAY"
        )
        for event in events:
            all_events.append(event)
            if journal is not None:
                journal.record_event(event)
            if publisher is not None:
                publisher.publish(event)
        previous = current

    return tuple(all_events)


# ---------------------------------------------------------------------------
# Live / incremental mode — one observation at a time, matching how
# Series 74's producer would feed this engine.
# ---------------------------------------------------------------------------
def generate_events_for_next_observation(
    new_observation: Observation,
    previous_observation: Optional[Observation],
    running_state: Optional[RunningState] = None,
    *,
    journal=None,
    publisher: Optional[MarketEventPublisher] = None,
) -> Tuple[MarketEvent, ...]:
    """Compare one newly-arrived Observation against the last-seen
    Observation for its series, generating and (optionally)
    journaling/publishing MarketEvents. Returns
    (events,) -- callers that need the updated RunningState should call
    `engine.compare_observations` directly (this wrapper exists to
    match the spec's exact signature for live/incremental use; see
    `LiveMarketEventStream` below for a stateful convenience wrapper
    that threads RunningState for you)."""
    events, _updated_state = engine.compare_observations(
        new_observation, previous_observation, running_state, detection_context="LIVE"
    )
    for event in events:
        if journal is not None:
            journal.record_event(event)
        if publisher is not None:
            publisher.publish(event)
    return events


class LiveMarketEventStream:
    """Stateful convenience wrapper around
    `generate_events_for_next_observation` for a single (observation_type,
    instrument) series in live/incremental mode -- carries `previous`
    and `RunningState` forward between calls so callers do not have to.
    Purely additive sugar; `generate_events_for_next_observation` itself
    remains the stateless, directly-testable entrypoint used for the
    replay/live parity proof."""

    def __init__(self, *, journal=None, publisher: Optional[MarketEventPublisher] = None) -> None:
        self._previous: Optional[Observation] = None
        self._running_state: Optional[RunningState] = None
        self.journal = journal
        self.publisher = publisher
        self.emitted: List[MarketEvent] = []

    def handle_observation(self, observation: Observation) -> Tuple[MarketEvent, ...]:
        events, self._running_state = engine.compare_observations(
            observation, self._previous, self._running_state, detection_context="LIVE"
        )
        self._previous = observation
        for event in events:
            self.emitted.append(event)
            if self.journal is not None:
                self.journal.record_event(event)
            if self.publisher is not None:
                self.publisher.publish(event)
        return events
