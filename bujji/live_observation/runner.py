"""Live Observation Producer Framework runner — Deliverable 2's Producer
Interface, plus Deliverable 6's publication-interface stubs, plus one
concrete synthetic reference Producer for the Deliverable 10
demonstration.

The Producer Interface is a `typing.Protocol` -- broker-neutral,
structural, and deliberately never importing any FYERS SDK type (see
`tests/test_live_observation_producer.py::TestIsolation` for the AST
check enforcing this). A real FYERS-backed Producer (wrapping
`bujji.broker.fyers_ws.FyersTickFeed`, per the Step 0 investigation
documented in `docs/LIVE_OBSERVATION_PRODUCER.md`) can be added later,
outside this module, without changing this Protocol.

Deliverable 6's Observation Recorder / Replay Recorder / MSI publication
interfaces are represented here as `typing.Protocol` stubs with no-op /
in-memory default implementations -- explicitly "no downstream coupling":
this module imports no real recorder/MSI code (none exists yet).
"""
from __future__ import annotations

from typing import List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from bujji.market_observation.models import Observation

from . import config as _config
from . import engine
from .models import AggregationWindow, LiveObservationEvent


# ---------------------------------------------------------------------------
# Deliverable 2 — Producer Interface (structural, broker-neutral)
# ---------------------------------------------------------------------------
@runtime_checkable
class ObservationProducer(Protocol):
    """Structural interface every live event producer must satisfy.
    Never imports a broker SDK type -- payloads/events are this
    package's own `LiveObservationEvent`, never a raw broker frame."""

    def start(self) -> None:
        """Begin producing events (idempotent, per FyersTickFeed's own
        established convention)."""
        ...

    def stop(self) -> None:
        """Stop producing events; safe to call even if never started."""
        ...

    def subscribe(self, instruments: Sequence[str]) -> None:
        """Request events for the given instruments."""
        ...

    def unsubscribe(self, instruments: Sequence[str]) -> None:
        """Stop requesting events for the given instruments."""
        ...

    def publish(self, event: LiveObservationEvent) -> None:
        """Hand one produced event to the pipeline (translation ->
        aggregation -> downstream publication)."""
        ...


# ---------------------------------------------------------------------------
# Deliverable 6 — publication interfaces (future; stubs only here, no
# downstream coupling -- none of these real systems exist yet).
# ---------------------------------------------------------------------------
@runtime_checkable
class ObservationRecorder(Protocol):
    def record(self, observation: Observation) -> None: ...


@runtime_checkable
class ReplayRecorder(Protocol):
    def record_event(self, event: LiveObservationEvent) -> None: ...


@runtime_checkable
class MarketStateIndexPublisher(Protocol):
    def publish(self, observation: Observation) -> None: ...


class InMemoryObservationRecorder:
    """No-op-safe, in-memory-collecting default ObservationRecorder --
    for tests/demonstration only, never a stand-in for a real MSI."""

    def __init__(self) -> None:
        self.recorded: List[Observation] = []

    def record(self, observation: Observation) -> None:
        self.recorded.append(observation)


class InMemoryReplayRecorder:
    def __init__(self) -> None:
        self.recorded: List[LiveObservationEvent] = []

    def record_event(self, event: LiveObservationEvent) -> None:
        self.recorded.append(event)


class NoOpMarketStateIndexPublisher:
    def __init__(self) -> None:
        self.published: List[Observation] = []

    def publish(self, observation: Observation) -> None:
        self.published.append(observation)


# ---------------------------------------------------------------------------
# Translation / aggregation / publication pipeline (composition only --
# reuses engine.py's pure functions, never re-implements them).
# ---------------------------------------------------------------------------
class LiveObservationPipeline:
    """Drives one event at a time through translate -> aggregate ->
    publish. Deterministic: every timestamp comes from the event data
    itself, never the wall clock. One window per (instrument, interval)
    at a time -- this sprint's minimal framework, not a multi-window
    scheduler."""

    def __init__(
        self,
        *,
        aggregation_interval: str = _config.DEFAULT_AGGREGATION_INTERVAL,
        source: str = _config.DEFAULT_SOURCE,
        observation_recorder: Optional[ObservationRecorder] = None,
        replay_recorder: Optional[ReplayRecorder] = None,
        msi_publisher: Optional[MarketStateIndexPublisher] = None,
        journal=None,
    ) -> None:
        self.aggregation_interval = aggregation_interval
        self.source = source
        self.observation_recorder = observation_recorder or InMemoryObservationRecorder()
        self.replay_recorder = replay_recorder or InMemoryReplayRecorder()
        self.msi_publisher = msi_publisher or NoOpMarketStateIndexPublisher()
        self.journal = journal
        self._windows: dict = {}  # (instrument, interval) -> AggregationWindow
        self.translated: List[Observation] = []
        self.closed_window_observations: List[Observation] = []

    def _publish(self, observation: Observation) -> None:
        self.observation_recorder.record(observation)
        self.msi_publisher.publish(observation)

    def handle_event(self, event: LiveObservationEvent) -> Optional[Observation]:
        """Process one event: journal it, translate it (if translatable),
        publish the translated Observation, and roll it into its
        aggregation window if it is a tick. Returns the translated
        Observation, if any."""
        if self.journal is not None:
            self.journal.record_event(event)
        self.replay_recorder.record_event(event)

        observation = engine.translate_event(event)

        if self.journal is not None:
            self.journal.record_translation(event, observation)

        if observation is not None:
            self.translated.append(observation)
            self._publish(observation)

        from .taxonomy import EVENT_TICK_RECEIVED

        if event.event_type == EVENT_TICK_RECEIVED:
            self._roll_into_window(event)

        return observation

    def _roll_into_window(self, event: LiveObservationEvent) -> None:
        from .models import Tick

        instrument = event.payload["instrument"]
        key = (instrument, self.aggregation_interval)
        window = self._windows.get(key)

        if window is None:
            window_start, window_end = _window_bounds(event.timestamp, self.aggregation_interval)
            window = engine.new_window(instrument, self.aggregation_interval, window_start, window_end)

        tick = Tick(timestamp=event.timestamp, price=event.payload["price"], volume=event.payload.get("volume"))

        if engine.should_close(window, event.timestamp) and window.ticks:
            self._close_and_publish(key, window)
            window_start, window_end = _window_bounds(event.timestamp, self.aggregation_interval)
            window = engine.new_window(instrument, self.aggregation_interval, window_start, window_end)

        window = engine.add_tick(window, tick)
        self._windows[key] = window

    def close_window(self, instrument: str) -> Optional[Observation]:
        """Force-close the current window for `instrument`, if any."""
        key = (instrument, self.aggregation_interval)
        window = self._windows.get(key)
        if window is None:
            return None
        return self._close_and_publish(key, window)

    def _close_and_publish(self, key, window: AggregationWindow) -> Optional[Observation]:
        closed, observation = engine.close_window(window, source=self.source)
        if self.journal is not None:
            self.journal.record_window_close(closed, observation)
        self._windows[key] = closed
        if observation is not None:
            self.closed_window_observations.append(observation)
            self._publish(observation)
        return observation


def _window_bounds(timestamp: str, interval: str) -> Tuple[str, str]:
    """Compute [window_start, window_end) for `timestamp` under
    `interval`, using ISO-8601 arithmetic only (no wall clock)."""
    from datetime import datetime, timedelta

    from .taxonomy import INTERVAL_SECONDS

    seconds = INTERVAL_SECONDS.get(interval)
    if seconds is None:
        # TICK interval: pass-through, one tick per "window".
        return timestamp, timestamp

    dt = datetime.fromisoformat(timestamp)
    epoch = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = (dt - epoch).total_seconds()
    window_index = int(elapsed // seconds)
    window_start_dt = epoch + timedelta(seconds=window_index * seconds)
    window_end_dt = window_start_dt + timedelta(seconds=seconds)
    return window_start_dt.isoformat(), window_end_dt.isoformat()


# ---------------------------------------------------------------------------
# Synthetic reference Producer -- for the Deliverable 10 demonstration
# and for tests. No real broker connection required (per Deliverable
# 10's explicit allowance). Deterministic: replays a pre-defined
# sequence of events in order; no time.sleep, no wall clock.
# ---------------------------------------------------------------------------
class SyntheticEventProducer:
    """Drives a fixed, hand-authored sequence of LiveObservationEvents
    through a LiveObservationPipeline, one at a time, in order. Implements
    the ObservationProducer Protocol structurally (start/stop/subscribe/
    unsubscribe/publish) without inheriting from it."""

    def __init__(self, events: Sequence[LiveObservationEvent], pipeline: LiveObservationPipeline) -> None:
        self._events = list(events)
        self._pipeline = pipeline
        self._subscribed: set = set()
        self._running = False

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def subscribe(self, instruments: Sequence[str]) -> None:
        self._subscribed.update(instruments)

    def unsubscribe(self, instruments: Sequence[str]) -> None:
        self._subscribed.difference_update(instruments)

    def publish(self, event: LiveObservationEvent) -> None:
        self._pipeline.handle_event(event)

    def run(self) -> List[Optional[Observation]]:
        """Drive every event in the fixed sequence through the pipeline,
        in order. Returns the list of translated Observations (or None
        for non-translatable events), one per input event."""
        if not self._running:
            self.start()
        results: List[Optional[Observation]] = []
        for event in self._events:
            results.append(self._pipeline.handle_event(event))
        return results
