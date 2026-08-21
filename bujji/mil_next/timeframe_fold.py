"""Timeframe folds -- BUJJI MIL Next.

`fold_timeframe(points, cutoff_event_time, timeframe, config)` is
called once per bucket, always with the SAME `decision_cutoff_event_time`
watermark (owned by snapshot_builder.build_snapshot -- never a
per-bucket-derived cutoff).

No-look-ahead is enforced ACTIVELY here (filter + defensive assert) and
is additionally proven BEHAVIORALLY by the test suite: a fold over an
input set including entries with event_time > cutoff must produce a
byte-identical RegimeAssessment to the same fold over that input set
with those entries physically removed. The filter+assert below is a
defensive runtime guard, not itself the proof.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from . import taxonomy as tx
from .models import MarketDataPoint, RegimeAssessment, TimeframeConfig


class LookAheadInvariantViolation(RuntimeError):
    """Defensive-only: should be unreachable if the filter step above it
    is correct. Never rely on this alone as the no-look-ahead proof --
    see the module docstring."""


def _window_start(points: Sequence[MarketDataPoint], timeframe: str, config: TimeframeConfig,
                   cutoff_event_time: datetime) -> datetime:
    window_seconds = config.bucket_definitions[timeframe]["window_seconds"]
    if window_seconds is None:  # SESSION -- start of the point set
        return min((p.event_time for p in points), default=cutoff_event_time)
    from datetime import timedelta
    return cutoff_event_time - timedelta(seconds=window_seconds)


def fold_timeframe(
    points: Sequence[MarketDataPoint],
    cutoff_event_time: datetime,
    timeframe: str,
    config: TimeframeConfig,
) -> RegimeAssessment:
    if timeframe not in config.buckets:
        raise ValueError(f"unknown timeframe {timeframe!r}")

    in_window = [p for p in points if p.event_time <= cutoff_event_time]
    if any(p.event_time > cutoff_event_time for p in in_window):
        raise LookAheadInvariantViolation("filter failed to exclude a future-relative-to-cutoff entry")

    window_start = _window_start(in_window, timeframe, config, cutoff_event_time)
    bucketed = [p for p in in_window if p.event_time >= window_start]

    if bucketed:
        window_end_event_time = max(p.event_time for p in bucketed)
        first_price = bucketed[0].price
        last_price = bucketed[-1].price
        if last_price > first_price:
            direction = tx.DIRECTION_BULLISH
        elif last_price < first_price:
            direction = tx.DIRECTION_BEARISH
        else:
            direction = tx.DIRECTION_NEUTRAL
    else:
        window_end_event_time = window_start
        direction = tx.DIRECTION_UNKNOWN

    lag_ms = (cutoff_event_time - window_end_event_time).total_seconds() * 1000.0

    return RegimeAssessment(
        timeframe=timeframe,
        direction=direction,
        regime=tx.REGIME_UNKNOWN,   # structural regime (trend/range/breakout) is PSI/MSSI-owned, not
                                     # computed here -- this laboratory's fold only derives directional
                                     # lean from raw price movement as a deterministic stand-in
        window_start_event_time=window_start,
        window_end_event_time=window_end_event_time,
        lag_ms=lag_ms,
        sample_count=len(bucketed),
        as_of_event_time=cutoff_event_time,
    )


def compute_timeframe_agreement(
    states: dict, config: TimeframeConfig,
) -> tuple:
    eligible = {}
    excluded = []
    for timeframe, assessment in states.items():
        limit = config.max_lag_ms.get(timeframe)
        if limit is not None and assessment.lag_ms > limit:
            excluded.append(timeframe)
            continue
        eligible[timeframe] = assessment

    if len(eligible) < 2:
        return tx.TIMEFRAME_AGREEMENT_INSUFFICIENT_EVIDENCE, tuple(excluded)

    directions = [a.direction for a in eligible.values()]
    if any(d == tx.DIRECTION_UNKNOWN for d in directions):
        return tx.TIMEFRAME_AGREEMENT_INSUFFICIENT_EVIDENCE, tuple(excluded)

    distinct = set(directions)
    if len(distinct) == 1:
        return tx.TIMEFRAME_AGREEMENT_UNANIMOUS, tuple(excluded)

    counts = {d: directions.count(d) for d in distinct}
    majority_count = max(counts.values())
    if majority_count > len(directions) / 2:
        return tx.TIMEFRAME_AGREEMENT_MAJORITY, tuple(excluded)
    return tx.TIMEFRAME_AGREEMENT_SPLIT, tuple(excluded)
