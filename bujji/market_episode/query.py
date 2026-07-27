"""Pure, read-only query helpers over recorded Episodes. No mutation,
no analytics beyond simple lookup -- mirrors
`bujji.live_market_events.query`'s discipline.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .models import Episode


def episode_by_id(episodes: Tuple[Episode, ...], episode_id: str) -> Optional[Episode]:
    """Returns the LATEST snapshot for episode_id present in `episodes`
    (callers typically pass the current open-episode set, which holds
    at most one snapshot per episode_id; if passed a full journal
    history with multiple snapshots for the same id, the last one
    found wins, matching append-order-is-recency)."""
    match: Optional[Episode] = None
    for e in episodes:
        if e.episode_id == episode_id:
            match = e
    return match


def episodes_by_state(episodes: Tuple[Episode, ...], state: str) -> Tuple[Episode, ...]:
    return tuple(e for e in episodes if e.current_state == state)


def episodes_in_time_range(episodes: Tuple[Episode, ...], start_timestamp: str, end_timestamp: str) -> Tuple[Episode, ...]:
    return tuple(e for e in episodes if start_timestamp <= e.latest_update <= end_timestamp)


def episodes_for_event(episodes: Tuple[Episode, ...], event_id: str) -> Tuple[Episode, ...]:
    return tuple(e for e in episodes if event_id in e.originating_event_ids)


def episodes_for_observation(episodes: Tuple[Episode, ...], observation_id: str) -> Tuple[Episode, ...]:
    return tuple(e for e in episodes if observation_id in e.originating_observation_ids)
