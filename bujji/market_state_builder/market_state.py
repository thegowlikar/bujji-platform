"""Market State -- Shadow Campaign v2 Phase 3B, updated Phase 10 (Market
Memory Integrity Upgrade).

ObservationMemory: the minimal state threaded from cycle to cycle so
event/episode detection has a real `previous` to diff against --
mirrors live_market_events.models.RunningState's own existing
threading pattern, not a new invention. Holds ONLY: the previous PRICE
Observation, the current PRICE Observation, the open Episode tuple, and
the accumulated MarketEvent history for this session. No strategy,
trade, position, or risk field exists here, ever.

PHASE 10: `event_history` was already being accumulated correctly here
the whole time (`event_history=self.event_history + new_events` below)
-- it was simply never PASSED anywhere. `process()` now forwards it to
`build_market_state_assessment()` so PSI/MSSI can resolve episodes'
historical `originating_event_ids` against the FULL session history,
not just this cycle's fresh delta. See assessment_bridge.py's module
docstring for the full root-cause trace.

Defined once here (not duplicated in event_bridge.py, despite the
original brief mentioning it in both places) -- event_bridge.py and
episode_bridge.py are pure functions that take/return plain
Observation/MarketEvent/Episode tuples; ObservationMemory is the single
place that threads state between cycles, avoiding two competing
definitions of the same concept.

MarketStateBuilder: the standalone orchestrator (MarketSnapshot ->
MarketStateAssessment) requested for independent validation before any
ShadowSessionRunner integration.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Tuple

from bujji.live_market_events.models import MarketEvent, RunningState
from bujji.market_episode.models import Episode
from bujji.market_observation.models import Observation

from .assessment_bridge import MarketStateAssessment, build_market_state_assessment
from .episode_bridge import fold_events_into_episodes
from .event_bridge import detect_events
from bujji.market_perception.models import MarketSnapshot
from .observation_bridge import build_observation_from_snapshot
from .option_observation_bridge import build_option_observations_from_snapshot


@dataclass(frozen=True)
class ObservationMemory:
    previous_observation: Optional[Observation] = None
    current_observation: Optional[Observation] = None
    open_episodes: Tuple[Episode, ...] = ()
    event_history: Tuple[MarketEvent, ...] = ()
    running_state: Optional[RunningState] = None

    def advance(
        self, new_observation: Observation, new_events: Tuple[MarketEvent, ...],
        updated_episodes: Tuple[Episode, ...], updated_running_state: Optional[RunningState],
    ) -> "ObservationMemory":
        """Returns a NEW ObservationMemory snapshot -- never mutates
        this one in place, matching every other frozen-state object in
        this codebase (e.g. Episode's own growth-by-snapshot design).
        running_state MUST be threaded forward too, or session-high/low
        detection silently resets every cycle (see event_bridge.py)."""
        return replace(
            self, previous_observation=self.current_observation, current_observation=new_observation,
            open_episodes=updated_episodes, event_history=self.event_history + new_events,
            running_state=updated_running_state,
        )


class MarketStateBuilder:
    """Standalone pipeline: MarketSnapshot -> MarketStateAssessment.
    NOT wired into ShadowSessionRunner -- call .process(snapshot) once
    per cycle from a test or a separate script during validation."""

    def __init__(self, memory: Optional[ObservationMemory] = None) -> None:
        self._memory = memory if memory is not None else ObservationMemory()
        # Cross-cycle memory for the two comparisons ObservationMemory does not
        # cover. It tracks the spot Observation for event detection; the option
        # chain and the futures basis need the same current-vs-previous
        # treatment and had nowhere to live, which is why MPPI's migration and
        # expansion lenses were dark and no basis-change signal existed.
        self._previous_option_observations: Optional[Tuple] = None
        self._previous_futures_basis: Optional[float] = None

    @property
    def memory(self) -> ObservationMemory:
        return self._memory

    def process(self, snapshot: MarketSnapshot) -> MarketStateAssessment:
        current = build_observation_from_snapshot(snapshot)
        # Compare against the LAST cycle's observation (memory.current_observation),
        # never memory.previous_observation -- that field is one cycle further
        # back by the time this method runs (ObservationMemory.advance() shifts
        # current -> previous only AFTER this comparison). Using the wrong field
        # here silently suppressed PRICE_CHANGED on every second cycle -- caught
        # by test_price_movement_across_two_cycles_produces_price_structure.
        events, updated_running_state = detect_events(
            current, self._memory.current_observation, self._memory.running_state,
        )
        episodes = fold_events_into_episodes(self._memory.open_episodes, events)
        option_observations = build_option_observations_from_snapshot(snapshot)

        if current is not None:
            self._memory = self._memory.advance(current, events, episodes, updated_running_state)
        # When current is None, detect_events() itself already returns
        # () (nothing to compare), so events/episodes are trivially
        # unchanged from self._memory.open_episodes here -- no separate
        # branch needed; previous/current_observation are simply left
        # untouched until a real spot value returns.

        # self._memory has already been advanced above (when current is
        # not None), so self._memory.event_history already includes
        # this cycle's fresh events too -- pass it as the FULL
        # accumulated history for PSI/MSSI's lookups, while `events`
        # (this cycle's delta) is still what's stored on
        # MarketStateAssessment.events, unchanged.
        futures_basis = getattr(getattr(snapshot, "futures", None), "basis", None)

        assessment = build_market_state_assessment(
            episodes, events, option_observations, snapshot.timestamp,
            event_history=self._memory.event_history,
            previous_option_observations=self._previous_option_observations,
            futures_basis=futures_basis,
            previous_futures_basis=self._previous_futures_basis,
        )

        # REMEMBER AFTER THE COMPARISON, never before -- the same ordering
        # discipline documented above for memory.advance(). Overwriting these
        # first would compare this cycle against itself and silently report no
        # change on every cycle.
        #
        # Only real values are retained: an empty chain or an absent futures
        # snapshot leaves the previous value standing, so one failed poll
        # costs a comparison rather than resetting the baseline to nothing.
        if option_observations:
            self._previous_option_observations = option_observations
        if futures_basis is not None:
            self._previous_futures_basis = futures_basis

        return assessment
