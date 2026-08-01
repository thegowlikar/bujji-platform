from datetime import datetime, timedelta, timezone

from bujji.mil_next import taxonomy as tx
from bujji.mil_next.models import MarketDataInputs, MarketDataPoint, SourceHealth, TimeframeConfig
from bujji.mil_next.snapshot_builder import build_snapshot


def _points(n, last_event_time, price_start=100.0):
    return tuple(
        MarketDataPoint(event_time=last_event_time - timedelta(seconds=(n - 1 - i) * 5), price=price_start + i)
        for i in range(n)
    )


def test_minimal_no_trade_path_on_mandatory_failure():
    last_point_time = datetime(2026, 7, 31, 9, 15, 0, tzinfo=timezone.utc)
    inputs = MarketDataInputs(
        session_id="S1", points_by_symbol={"NIFTY": _points(3, last_point_time)},
        source_health={"underlying_tick": SourceHealth(connected=True)},
        max_silence_ms={"underlying_tick": 600_000.0},
    )
    cutoff = datetime(2026, 7, 31, 9, 20, 0, tzinfo=timezone.utc)
    snap = build_snapshot(
        "S1", "C1", cutoff, inputs, TimeframeConfig(config_version="v1"), (), {"x": "1"},
        clock=lambda: last_point_time + timedelta(seconds=40),  # arrival_age exceeds 30s STALE threshold
    )
    assert snap.posture == tx.POSTURE_NO_TRADE
    assert snap.data_quality.mandatory_data_failure_reason is not None
    assert snap.primary_thesis == tx.NOT_EVALUATED_DUE_TO_DATA_FAILURE
    assert snap.timeframe_states == tx.NOT_EVALUATED_DUE_TO_DATA_FAILURE
    assert snap.known_event_risk_state in tx.ALL_EVENT_RISK_STATES  # still evaluated
    assert snap.content_hash


def test_full_path_produces_real_posture():
    last_point_time = datetime(2026, 7, 31, 9, 19, 55, tzinfo=timezone.utc)
    inputs = MarketDataInputs(
        session_id="S2", points_by_symbol={"NIFTY": _points(10, last_point_time)},
        source_health={"underlying_tick": SourceHealth(connected=True)},
        max_silence_ms={"underlying_tick": 600_000.0},
    )
    cutoff = datetime(2026, 7, 31, 9, 20, 0, tzinfo=timezone.utc)
    snap = build_snapshot(
        "S2", "C1", cutoff, inputs, TimeframeConfig(config_version="v1"), (), {"x": "1"},
        clock=lambda: last_point_time + timedelta(seconds=2),
    )
    assert snap.posture in tx.ALL_POSTURES
    assert snap.primary_thesis != tx.NOT_EVALUATED_DUE_TO_DATA_FAILURE
    assert isinstance(snap.timeframe_states, dict)
    assert set(snap.timeframe_states.keys()) == set(tx.ALL_TIMEFRAMES)


def test_liquidity_unknown_caps_posture_at_reduced_or_worse():
    last_point_time = datetime(2026, 7, 31, 9, 19, 55, tzinfo=timezone.utc)
    inputs = MarketDataInputs(
        session_id="S3", points_by_symbol={"NIFTY": _points(10, last_point_time)},
        source_health={"underlying_tick": SourceHealth(connected=True)},
        max_silence_ms={"underlying_tick": 600_000.0},
    )
    cutoff = datetime(2026, 7, 31, 9, 20, 0, tzinfo=timezone.utc)
    snap = build_snapshot(
        "S3", "C1", cutoff, inputs, TimeframeConfig(config_version="v1"), (), {"x": "1"},
        clock=lambda: last_point_time + timedelta(seconds=2),
    )
    # liquidity_posture is always LIQUIDITY_UNKNOWN in this laboratory (no depth feed) --
    # posture must never be NORMAL as a result.
    assert snap.liquidity_posture == tx.LIQUIDITY_UNKNOWN
    assert snap.posture != tx.POSTURE_NORMAL


def test_replay_determinism_two_identical_builds_same_hash():
    last_point_time = datetime(2026, 7, 31, 9, 19, 55, tzinfo=timezone.utc)
    inputs = MarketDataInputs(
        session_id="S4", points_by_symbol={"NIFTY": _points(10, last_point_time)},
        source_health={"underlying_tick": SourceHealth(connected=True)},
        max_silence_ms={"underlying_tick": 600_000.0},
    )
    cutoff = datetime(2026, 7, 31, 9, 20, 0, tzinfo=timezone.utc)
    config = TimeframeConfig(config_version="v1")
    snap1 = build_snapshot("S4", "C1", cutoff, inputs, config, (), {"x": "1"},
                            clock=lambda: last_point_time + timedelta(seconds=1))
    snap2 = build_snapshot("S4", "C1", cutoff, inputs, config, (), {"x": "1"},
                            clock=lambda: last_point_time + timedelta(seconds=3))
    assert snap1.content_hash == snap2.content_hash
    assert snap1.idempotency_key == snap2.idempotency_key
