"""Pure, read-only query helpers over ObservationSeries.

No mutation, no derived analytics (no swing detection, no averages,
nothing MSI/Derived-Evidence-shaped) -- only lookups over what is
already stored, per MOF Deliverable 1's Observation/Derived-Evidence
boundary.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .models import Observation, ObservationSeries, SeriesGap


def at_or_before(series: ObservationSeries, timestamp: str) -> Optional[Observation]:
    """The latest Observation in the series with timestamp <= the given
    timestamp, or None if no such Observation exists. Assumes the
    series is timestamp-ordered (callers that appended out-of-order
    data should validate ordering first, per engine.validate_series_ordering).
    """
    candidate: Optional[Observation] = None
    for observation in series.observations:
        if observation.identity.timestamp <= timestamp:
            if candidate is None or observation.identity.timestamp > candidate.identity.timestamp:
                candidate = observation
        else:
            break
    return candidate


def in_window(series: ObservationSeries, start: str, end: str) -> Tuple[Observation, ...]:
    """All Observations with start <= timestamp <= end, inclusive."""
    return tuple(o for o in series.observations if start <= o.identity.timestamp <= end)


def gaps_in_window(series: ObservationSeries, start: str, end: str) -> Tuple[SeriesGap, ...]:
    """All recorded SeriesGap markers whose after_timestamp falls
    within [start, end]."""
    return tuple(g for g in series.gaps if start <= g.after_timestamp <= end)


def latest(series: ObservationSeries) -> Optional[Observation]:
    if not series.observations:
        return None
    return series.observations[-1]


def earliest(series: ObservationSeries) -> Optional[Observation]:
    if not series.observations:
        return None
    return series.observations[0]


def count(series: ObservationSeries) -> int:
    return len(series.observations)
