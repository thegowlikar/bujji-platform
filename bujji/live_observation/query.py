"""Pure, read-only query helpers over recorded LiveObservationEvents /
ProducerState. No mutation, no analytics -- only lookups over what is
already recorded, mirroring `bujji.market_observation.query`'s
discipline.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .models import LiveObservationEvent, ProducerState, ProducerStateTransition


def events_since(events: Tuple[LiveObservationEvent, ...], timestamp: str) -> Tuple[LiveObservationEvent, ...]:
    """All events with timestamp >= the given timestamp, in recorded order."""
    return tuple(e for e in events if e.timestamp >= timestamp)


def events_of_type(events: Tuple[LiveObservationEvent, ...], event_type: str) -> Tuple[LiveObservationEvent, ...]:
    return tuple(e for e in events if e.event_type == event_type)


def latest_event(events: Tuple[LiveObservationEvent, ...]) -> Optional[LiveObservationEvent]:
    if not events:
        return None
    return events[-1]


def current_state(state: ProducerState) -> str:
    return state.current_state


def transitions_since(state: ProducerState, timestamp: str) -> Tuple[ProducerStateTransition, ...]:
    return tuple(t for t in state.history if t.timestamp >= timestamp)


def was_ever_in_state(state: ProducerState, target_state: str) -> bool:
    if state.current_state == target_state:
        return True
    return any(t.to_state == target_state for t in state.history)
