"""Memory Health Telemetry -- Phase 10, Task 3.

Pure, read-only observability over the SAME episodes/event_history
MarketStateBuilder already produces -- no new state, no new broker
call, no coupling into any decision path. Answers one question every
cycle: is Bujji reasoning over COMPLETE memory or PARTIAL memory right
now? Never silently degrades -- an unresolved reference is always
surfaced, never hidden or assumed benign.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from bujji.live_market_events.models import MarketEvent
from bujji.market_episode.models import Episode


@dataclass(frozen=True)
class MemoryHealth:
    episode_count: int
    active_events: int
    historical_events_available: int
    historical_events_referenced: int
    unresolved_event_references: int
    history_resolution_ratio: float

    def to_dict(self) -> dict:
        return {
            "episode_count": self.episode_count,
            "active_events": self.active_events,
            "historical_events_available": self.historical_events_available,
            "historical_events_referenced": self.historical_events_referenced,
            "unresolved_event_references": self.unresolved_event_references,
            "history_resolution_ratio": self.history_resolution_ratio,
        }


def compute_memory_health(
    episodes: Tuple[Episode, ...],
    events: Tuple[MarketEvent, ...],
    event_history: Tuple[MarketEvent, ...],
) -> MemoryHealth:
    """`episodes`/`events` are exactly `MarketStateAssessment.episodes`/
    `.events` (this cycle's delta); `event_history` is the builder's
    own accumulated `ObservationMemory.event_history` -- the same
    tuple Phase 10's fix now threads into PSI/MSSI. Referenced ids that
    fail to resolve against `event_history` would mean the Phase 10 fix
    itself has a gap -- surfaced here, never assumed away."""
    available_ids = {e.event_id for e in event_history}
    referenced_ids = {eid for ep in episodes for eid in ep.originating_event_ids}
    unresolved = referenced_ids - available_ids

    referenced_count = len(referenced_ids)
    resolved_count = referenced_count - len(unresolved)
    ratio = 1.0 if referenced_count == 0 else round(resolved_count / referenced_count, 4)

    return MemoryHealth(
        episode_count=len(episodes),
        active_events=len(events),
        historical_events_available=len(available_ids),
        historical_events_referenced=referenced_count,
        unresolved_event_references=len(unresolved),
        history_resolution_ratio=ratio,
    )
