from datetime import datetime, timedelta, timezone

from bujji.mil_next import taxonomy as tx
from bujji.mil_next.data_quality import (
    build_data_quality_context,
    classify_completeness,
    classify_event_time_trust,
    classify_feed_disagreement,
    is_mandatory_failure,
)
from bujji.mil_next.models import MarketDataPoint, SourceHealth


def _pt(sec, price=100.0, seq=None):
    return MarketDataPoint(event_time=datetime(2026, 7, 31, 9, 15, sec, tzinfo=timezone.utc), price=price, sequence_no=seq)


def test_quiet_market_is_unknown_not_incomplete():
    points = [_pt(0), _pt(30)]
    now = datetime(2026, 7, 31, 9, 15, 40, tzinfo=timezone.utc)
    state, detail = classify_completeness(points, source_health=None, max_silence_ms=60_000, now=now)
    assert state == tx.COMPLETENESS_UNKNOWN
    assert detail is None


def test_sequence_gap_detected():
    points = [_pt(0, seq=1), _pt(1, seq=2), _pt(2, seq=5)]
    now = datetime(2026, 7, 31, 9, 15, 3, tzinfo=timezone.utc)
    state, detail = classify_completeness(points, None, 60_000, now)
    assert state == tx.COMPLETENESS_GAP_DETECTED
    assert detail["gap_size"] == 2


def test_connected_no_gap_signal():
    points = [_pt(0)]
    now = datetime(2026, 7, 31, 9, 15, 1, tzinfo=timezone.utc)
    health = SourceHealth(connected=True, reconnected_since_last_observation=False)
    state, _ = classify_completeness(points, health, 60_000, now)
    assert state == tx.COMPLETENESS_CONNECTED_NO_GAP_SIGNAL


def test_max_silence_exceeded():
    points = [_pt(0)]
    now = datetime(2026, 7, 31, 9, 20, 0, tzinfo=timezone.utc)
    state, detail = classify_completeness(points, None, 60_000, now)
    assert state == tx.COMPLETENESS_MAX_SILENCE_EXCEEDED
    assert detail["silent_for_ms"] > 60_000


def test_synthetic_event_time_never_stores_zero_latency():
    trust = classify_event_time_trust(
        event_time=datetime(2026, 7, 31, 9, 15, 0, tzinfo=timezone.utc),
        receipt_time=datetime(2026, 7, 31, 9, 15, 0, tzinfo=timezone.utc),
        event_time_synthetic=True,
    )
    assert trust["transport_latency_ms"] is None
    assert trust["transport_latency_status"] == tx.TRANSPORT_LATENCY_NOT_MEASURABLE_SYNTHETIC_EVENT_TIME


def test_clock_skew_status_is_distinct_from_measured():
    trust = classify_event_time_trust(
        event_time=datetime(2026, 7, 31, 9, 15, 5, tzinfo=timezone.utc),   # event_time AHEAD of receipt
        receipt_time=datetime(2026, 7, 31, 9, 15, 0, tzinfo=timezone.utc),
        event_time_synthetic=False,
        skew_tolerance_ms=50.0,
    )
    assert trust["clock_skew_detected"] is True
    assert trust["transport_latency_status"] == tx.TRANSPORT_LATENCY_MEASURED_BUT_UNTRUSTED_CLOCK_SKEW


def test_skew_within_tolerance_is_measured():
    trust = classify_event_time_trust(
        event_time=datetime(2026, 7, 31, 9, 15, 0, 10000, tzinfo=timezone.utc),
        receipt_time=datetime(2026, 7, 31, 9, 15, 0, 30000, tzinfo=timezone.utc),
        event_time_synthetic=False,
        skew_tolerance_ms=50.0,
    )
    assert trust["clock_skew_detected"] is False
    assert trust["transport_latency_status"] == tx.TRANSPORT_LATENCY_MEASURED


def test_feed_disagreement_states():
    assert classify_feed_disagreement(None) == tx.FEED_DISAGREEMENT_NONE
    assert classify_feed_disagreement(5.0, tolerance_bps=25.0) == tx.FEED_DISAGREEMENT_WITHIN_TOLERANCE
    assert classify_feed_disagreement(50.0, tolerance_bps=25.0) == tx.FEED_DISAGREEMENT_BREACHED


def test_synthetic_source_freshness_ignores_fabricated_latency():
    # A source whose only signal path would wrongly look FRESH if latency
    # were treated as 0 -- but arrival_age itself is stale, and must
    # correctly override any (absent) latency signal.
    stale_points = [_pt(0)]
    receipt_time = datetime(2026, 7, 31, 9, 16, 0, tzinfo=timezone.utc)  # 60s later
    dq = build_data_quality_context(
        stale_points, source_health=None, max_silence_ms=600_000,
        receipt_time=receipt_time, event_time_synthetic=True,
    )
    assert dq.transport_latency_ms is None
    assert dq.transport_latency_status == tx.TRANSPORT_LATENCY_NOT_MEASURABLE_SYNTHETIC_EVENT_TIME
    assert dq.freshness == tx.FRESHNESS_STALE  # arrival_age alone correctly fails it


def test_mandatory_failure_detection():
    points = [_pt(0)]
    receipt_time = datetime(2026, 7, 31, 9, 20, 0, tzinfo=timezone.utc)
    dq = build_data_quality_context(points, None, 60_000, receipt_time)
    assert is_mandatory_failure(dq) is not None
