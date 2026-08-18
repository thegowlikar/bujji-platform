"""Observation Completeness Monitor — Phase 17E scope item 6.

Measures CAPTURE, never market behaviour.

Reports exactly four things:
  * expected observations   (from an explicit interval + window)
  * received observations   (what Layer 0 actually holds)
  * missing intervals       (which windows produced nothing)
  * source health           (the FEED's delivery rate)

`source_health` describes the data feed and nothing else. A DEGRADED
feed says nothing whatsoever about price, volatility, trend, or market
condition -- reading it as a market signal would be exactly the
category error this package exists to prevent. There is deliberately no
function here that looks at a price.

No wall-clock is read: the window is always supplied by the caller.
"""
from __future__ import annotations

import datetime
from typing import Iterable, List, Optional, Tuple

from . import taxonomy
from .models import CompletenessReport, RawObservation


def _parse(value: str) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _effective_timestamp(obs: RawObservation) -> Optional[datetime.datetime]:
    """Prefer event time; fall back to capture time only when the source
    genuinely published none. Which one was used is not guessed away --
    the underlying record retains both, distinctly."""
    stamp = obs.lineage.event_timestamp or obs.lineage.capture_timestamp
    return _parse(stamp)


def expected_interval_count(
    window_start: str, window_end: str, interval_seconds: int
) -> int:
    """How many intervals fit in [start, end). Returns 0 for a
    non-positive interval or an inverted/empty window rather than
    raising -- an unanswerable question yields zero expectation, never a
    fabricated one."""
    if interval_seconds <= 0:
        return 0
    start = _parse(window_start)
    end = _parse(window_end)
    if start is None or end is None:
        return 0
    if (start.tzinfo is None) != (end.tzinfo is None):
        return 0
    span = (end - start).total_seconds()
    if span <= 0:
        return 0
    return int(span // interval_seconds)


def measure(
    observations: Iterable[RawObservation],
    *,
    instrument: str,
    kind: str,
    window_start: str,
    window_end: str,
    interval_seconds: int,
) -> CompletenessReport:
    """Bucket the supplied observations into fixed intervals across the
    window and report which buckets received nothing.

    Only observations matching `instrument` and `kind` are counted --
    completeness of a NIFTY tick feed says nothing about an option chain
    feed, and conflating them would report a healthy feed as degraded.
    """
    start = _parse(window_start)
    expected = expected_interval_count(window_start, window_end, interval_seconds)

    matching: List[datetime.datetime] = []
    for obs in observations:
        if obs.instrument != instrument or obs.kind != kind:
            continue
        stamp = _effective_timestamp(obs)
        if stamp is None or start is None:
            continue
        if (stamp.tzinfo is None) != (start.tzinfo is None):
            continue
        offset = (stamp - start).total_seconds()
        if offset < 0:
            continue
        if expected and offset >= expected * interval_seconds:
            continue
        matching.append(stamp)

    filled_buckets = set()
    if start is not None and interval_seconds > 0:
        for stamp in matching:
            offset = (stamp - start).total_seconds()
            filled_buckets.add(int(offset // interval_seconds))

    missing: List[Tuple[str, str]] = []
    if start is not None and interval_seconds > 0:
        for index in range(expected):
            if index in filled_buckets:
                continue
            bucket_start = start + datetime.timedelta(seconds=index * interval_seconds)
            bucket_end = bucket_start + datetime.timedelta(seconds=interval_seconds)
            missing.append((bucket_start.isoformat(), bucket_end.isoformat()))

    received = len(matching)
    return CompletenessReport(
        instrument=instrument,
        kind=kind,
        window_start=window_start,
        window_end=window_end,
        interval_seconds=interval_seconds,
        expected_count=expected,
        received_count=received,
        missing_intervals=tuple(missing),
        source_health=classify_source_health(expected, len(filled_buckets)),
    )


def classify_source_health(expected_count: int, filled_count: int) -> str:
    """A mechanical ratio over delivery, nothing more.

    SILENT   -- something was expected and literally nothing arrived.
    HEALTHY  -- at least SOURCE_HEALTHY_MIN_RATIO of expected intervals
                produced at least one observation.
    DEGRADED -- anything in between.

    When nothing was expected, the feed is HEALTHY by definition: there
    is no evidence of a problem, and inventing one would be as dishonest
    as hiding a real gap.
    """
    if expected_count <= 0:
        return taxonomy.SOURCE_HEALTHY
    if filled_count <= 0:
        return taxonomy.SOURCE_SILENT
    if filled_count >= expected_count * taxonomy.SOURCE_HEALTHY_MIN_RATIO:
        return taxonomy.SOURCE_HEALTHY
    return taxonomy.SOURCE_DEGRADED
