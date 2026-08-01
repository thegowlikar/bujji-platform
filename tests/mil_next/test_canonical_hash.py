from dataclasses import replace
from datetime import datetime, timedelta, timezone

from bujji.mil_next import taxonomy as tx
from bujji.mil_next.canonical_hash import compute_content_hash
from bujji.mil_next.models import MarketDataInputs, SourceHealth, TimeframeConfig
from bujji.mil_next.snapshot_builder import build_snapshot

_LAST_POINT_TIME = datetime(2026, 7, 31, 9, 15, 20, tzinfo=timezone.utc)  # last of 5 points, 5s apart


def _inputs(prices):
    from bujji.mil_next.models import MarketDataPoint
    base = _LAST_POINT_TIME - timedelta(seconds=(len(prices) - 1) * 5)
    points = tuple(
        MarketDataPoint(event_time=base + timedelta(seconds=i * 5), price=p)
        for i, p in enumerate(prices)
    )
    return MarketDataInputs(
        session_id="SESSION-1",
        points_by_symbol={"NIFTY": points},
        source_health={"underlying_tick": SourceHealth(connected=True)},
        max_silence_ms={"underlying_tick": 600_000.0},
    )


def _build(clock_dt):
    cutoff = datetime(2026, 7, 31, 9, 20, 0, tzinfo=timezone.utc)
    config = TimeframeConfig(config_version="v1")
    return build_snapshot(
        session_id="SESSION-1", cadence_id="CADENCE-1", decision_cutoff_event_time=cutoff,
        market_data_inputs=_inputs([100.0, 101.0, 102.0, 103.0, 104.0]),
        timeframe_config=config, calendar_sources=(), active_config_versions={"x": "1"},
        clock=lambda: clock_dt,
    )


def test_hash_stable_across_different_wall_clock_receipt_times():
    # Both receipt times stay within the FRESH threshold (arrival_age well
    # under 30s), but differ enough that the raw arrival_age_ms figures are
    # genuinely different numbers -- proving those raw numbers are excluded
    # from the hash while the shared conclusion drives equality.
    snap1 = _build(_LAST_POINT_TIME + timedelta(seconds=1))
    snap2 = _build(_LAST_POINT_TIME + timedelta(seconds=5))
    assert snap1.data_quality.arrival_age_ms != snap2.data_quality.arrival_age_ms  # raw timings genuinely differ
    assert snap1.data_quality.freshness == snap2.data_quality.freshness == tx.FRESHNESS_FRESH
    assert snap1.content_hash == snap2.content_hash  # but the decision content is identical


def test_hash_differs_when_quality_conclusions_differ():
    snap_normal = _build(_LAST_POINT_TIME + timedelta(seconds=1))
    # Force a different freshness conclusion: arrival_age crosses the 30s
    # STALE threshold, driving the minimal NO_TRADE path.
    snap_stale = _build(_LAST_POINT_TIME + timedelta(seconds=40))
    assert snap_normal.data_quality.freshness != snap_stale.data_quality.freshness
    assert snap_normal.posture != snap_stale.posture
    assert snap_normal.content_hash != snap_stale.content_hash


def test_hash_excludes_identifiers():
    snap = _build(_LAST_POINT_TIME + timedelta(seconds=1))
    recomputed = compute_content_hash(replace(snap, snapshot_id="DIFFERENT-ID", idempotency_key="DIFFERENT-KEY"))
    assert recomputed == snap.content_hash
