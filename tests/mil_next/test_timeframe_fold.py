from datetime import datetime, timedelta, timezone

import pytest

from bujji.mil_next import taxonomy as tx
from bujji.mil_next.models import MarketDataPoint, TimeframeConfig
from bujji.mil_next.timeframe_fold import (
    LookAheadInvariantViolation,
    compute_timeframe_agreement,
    fold_timeframe,
)


def _pt(offset_sec, price):
    base = datetime(2026, 7, 31, 9, 15, 0, tzinfo=timezone.utc)
    return MarketDataPoint(event_time=base + timedelta(seconds=offset_sec), price=price)


def test_no_look_ahead_behavioral_equivalence():
    cutoff = datetime(2026, 7, 31, 9, 15, 30, tzinfo=timezone.utc)
    config = TimeframeConfig(config_version="v1")
    past = [_pt(0, 100.0), _pt(10, 101.0), _pt(20, 102.0)]
    future = [_pt(40, 999.0), _pt(50, 1.0)]

    result_with_future = fold_timeframe(past + future, cutoff, tx.TIMEFRAME_1M, config)
    result_without_future = fold_timeframe(past, cutoff, tx.TIMEFRAME_1M, config)
    assert result_with_future == result_without_future


def test_no_look_ahead_adversarial_running_state_not_leaked():
    """A naive filter-then-assert implementation could still leak future
    data into a running/cached intermediate (e.g. a running max/min).
    This fixture is adversarial to that specific failure mode: the
    future entries contain an extreme price that would change a
    running max/min if leaked anywhere."""
    cutoff = datetime(2026, 7, 31, 9, 15, 30, tzinfo=timezone.utc)
    config = TimeframeConfig(config_version="v1")
    past = [_pt(0, 100.0), _pt(20, 105.0)]
    future_extreme = [_pt(40, 999999.0)]

    result_with_future = fold_timeframe(past + future_extreme, cutoff, tx.TIMEFRAME_1M, config)
    result_without_future = fold_timeframe(past, cutoff, tx.TIMEFRAME_1M, config)
    assert result_with_future.direction == result_without_future.direction
    assert result_with_future.window_end_event_time == result_without_future.window_end_event_time


def test_lag_recorded_for_completed_bar_endpoint():
    cutoff = datetime(2026, 7, 31, 9, 30, 0, tzinfo=timezone.utc)
    config = TimeframeConfig(config_version="v1")
    points = [_pt(0, 100.0), _pt(5, 101.0)]  # both well before a 15m window relative to cutoff
    result = fold_timeframe(points, cutoff, tx.TIMEFRAME_15M, config)
    assert result.window_end_event_time < cutoff
    assert result.lag_ms > 0


def test_timeframe_agreement_unanimous():
    base_assessment = lambda direction, lag=0.0: type("A", (), {"direction": direction, "lag_ms": lag})()
    states = {
        tx.TIMEFRAME_1M: base_assessment(tx.DIRECTION_BULLISH),
        tx.TIMEFRAME_5M: base_assessment(tx.DIRECTION_BULLISH),
        tx.TIMEFRAME_15M: base_assessment(tx.DIRECTION_BULLISH),
        tx.TIMEFRAME_SESSION: base_assessment(tx.DIRECTION_BULLISH),
    }
    config = TimeframeConfig(config_version="v1")
    agreement, excluded = compute_timeframe_agreement(states, config)
    assert agreement == tx.TIMEFRAME_AGREEMENT_UNANIMOUS
    assert excluded == ()


def test_timeframe_agreement_excludes_high_lag_bucket():
    base_assessment = lambda direction, lag=0.0: type("A", (), {"direction": direction, "lag_ms": lag})()
    config = TimeframeConfig(config_version="v1")
    states = {
        tx.TIMEFRAME_1M: base_assessment(tx.DIRECTION_BULLISH, lag=0.0),
        tx.TIMEFRAME_5M: base_assessment(tx.DIRECTION_BULLISH, lag=0.0),
        tx.TIMEFRAME_15M: base_assessment(tx.DIRECTION_BEARISH, lag=999_999_999.0),  # exceeds max_lag_ms
    }
    agreement, excluded = compute_timeframe_agreement(states, config)
    assert tx.TIMEFRAME_15M in excluded
    assert agreement == tx.TIMEFRAME_AGREEMENT_UNANIMOUS  # only the two agreeing eligible buckets remain


def test_timeframe_agreement_insufficient_when_fewer_than_two_eligible():
    base_assessment = lambda direction, lag=0.0: type("A", (), {"direction": direction, "lag_ms": lag})()
    config = TimeframeConfig(config_version="v1")
    states = {
        tx.TIMEFRAME_1M: base_assessment(tx.DIRECTION_BULLISH, lag=999_999_999.0),
        tx.TIMEFRAME_SESSION: base_assessment(tx.DIRECTION_BEARISH, lag=999_999_999.0),
    }
    agreement, excluded = compute_timeframe_agreement(states, config)
    assert agreement == tx.TIMEFRAME_AGREEMENT_INSUFFICIENT_EVIDENCE
