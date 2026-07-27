"""Pure, read-only query helpers over recorded MarketEvents. No
mutation, no analytics beyond simple lookup -- mirrors
`bujji.live_observation.query`'s discipline.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .models import MarketEvent


def events_of_type(events: Tuple[MarketEvent, ...], event_type: str) -> Tuple[MarketEvent, ...]:
    return tuple(e for e in events if e.event_type == event_type)


def events_for_observation(events: Tuple[MarketEvent, ...], observation_id: str) -> Tuple[MarketEvent, ...]:
    return tuple(e for e in events if observation_id in e.originating_observation_ids)


def events_in_time_range(events: Tuple[MarketEvent, ...], start_timestamp: str, end_timestamp: str) -> Tuple[MarketEvent, ...]:
    return tuple(e for e in events if start_timestamp <= e.timestamp <= end_timestamp)


def latest_event(events: Tuple[MarketEvent, ...]) -> Optional[MarketEvent]:
    if not events:
        return None
    return events[-1]


def event_by_id(events: Tuple[MarketEvent, ...], event_id: str) -> Optional[MarketEvent]:
    for e in events:
        if e.event_id == event_id:
            return e
    return None
