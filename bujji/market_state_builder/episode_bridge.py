"""Episode Bridge -- Shadow Campaign v2 Phase 3B.

Folds each MarketEvent into the current open-episode tuple via the
EXISTING, unmodified market_episode.engine.process_event() -- no
custom episode/grouping logic here. process_event() itself already
filters to EPISODE_FORMING_EVENT_TYPES internally, so non-forming
events (lifecycle/gap/duplicate) are correctly ignored without this
bridge needing its own filtering logic.
"""
from __future__ import annotations

from typing import Tuple

from bujji.live_market_events.models import MarketEvent
from bujji.market_episode.engine import process_event
from bujji.market_episode.models import Episode


def fold_events_into_episodes(
    open_episodes: Tuple[Episode, ...], events: Tuple[MarketEvent, ...],
) -> Tuple[Episode, ...]:
    """Applies process_event() once per event, threading the episode
    tuple forward -- matching market_episode's own documented live-mode
    usage pattern (its own runner.py incremental entrypoint folds one
    event at a time, the same way)."""
    episodes = open_episodes
    for event in events:
        episodes = process_event(episodes, event)
    return episodes
