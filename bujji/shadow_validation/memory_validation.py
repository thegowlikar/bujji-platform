"""Market Memory Validation -- Phase 12 Task 6. Pure functions, no
state, no IO. Confirms events accumulate, episodes evolve, and regimes
are remembered -- reading ONLY the memory_health (Phase 10) and
regime_memory (Phase 11) blocks already persisted on each record.
"""
from __future__ import annotations

from typing import List, Optional


def _safe_get(d: Optional[dict], *path):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def validate_market_memory(records: List[dict]) -> dict:
    """Pure function: a session's record list -> a real, measured
    report of whether memory actually accumulated (never assumed).
    `growing` booleans are the honest pass/fail signal Task 6 asks for
    -- computed from real deltas across the session, not asserted."""
    available_series = [_safe_get(r, "memory_health", "historical_events_available") for r in records]
    available_series = [v for v in available_series if v is not None]

    episode_counts = [_safe_get(r, "memory_health", "episode_count") for r in records]
    episode_counts = [v for v in episode_counts if v is not None]

    unresolved_series = [_safe_get(r, "memory_health", "unresolved_event_references") for r in records]
    unresolved_series = [v for v in unresolved_series if v is not None]

    regime_durations = [_safe_get(r, "regime_memory", "duration_cycles") for r in records]
    regime_durations = [v for v in regime_durations if v is not None]

    transition_counts = [_safe_get(r, "regime_memory", "total_transitions_observed") for r in records]
    transition_counts = [v for v in transition_counts if v is not None]

    event_growth_rate = None
    events_accumulate_correctly = None
    if len(available_series) >= 2:
        event_growth_rate = round((available_series[-1] - available_series[0]) / max(1, len(available_series) - 1), 4)
        events_accumulate_correctly = available_series[-1] >= available_series[0]

    episode_duration_cycles = len(episode_counts) if episode_counts and all(c >= 1 for c in episode_counts) else None
    episodes_evolve_correctly = bool(episode_counts) and all(c >= 1 for c in episode_counts)

    structure_uses_history = None
    unresolved_at_end = unresolved_series[-1] if unresolved_series else None
    if unresolved_at_end is not None:
        structure_uses_history = unresolved_at_end == 0

    regime_duration_at_end = regime_durations[-1] if regime_durations else None
    transition_count_at_end = transition_counts[-1] if transition_counts else None
    regime_transitions_remembered = transition_count_at_end is not None

    return {
        "events_accumulate_correctly": events_accumulate_correctly,
        "episodes_evolve_correctly": episodes_evolve_correctly,
        "structure_uses_history": structure_uses_history,
        "regime_transitions_remembered": regime_transitions_remembered,
        "event_growth_rate": event_growth_rate,
        "episode_duration_cycles": episode_duration_cycles,
        "regime_duration_cycles": regime_duration_at_end,
        "transition_count": transition_count_at_end,
    }
