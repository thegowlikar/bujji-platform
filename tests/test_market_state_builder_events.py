"""Tests -- event_bridge.py, Shadow Campaign v2 Phase 3B."""
from __future__ import annotations

from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_observation.engine import build_observation
from bujji.market_state_builder.event_bridge import detect_events

TS1 = "2026-08-03T09:15:00+05:30"
TS2 = "2026-08-03T09:16:00+05:30"


def price_obs(value, ts):
    return build_observation(
        observation_type=moc_taxonomy.TYPE_PRICE, instrument="NIFTY", exchange="NSE",
        segment="INDEX", timestamp=ts, resolution=moc_taxonomy.RESOLUTION_TICK,
        source="fyers_live", schema_version="1.0", value_kind=moc_taxonomy.VALUE_KIND_SCALAR,
        payload=value, completeness=1.0, freshness=0.0, confidence=None, missing_fields=(),
        validation_status=moc_taxonomy.VALIDATION_VALID, source_quality=moc_taxonomy.SOURCE_QUALITY_HIGH,
        originating_source="fyers_live", acquisition_timestamp=ts, normalization_timestamp=ts,
        origin=moc_taxonomy.ORIGIN_LIVE, provenance_version="1.0",
    )


def test_first_cycle_no_previous_no_crash_returns_events_and_state():
    events, state = detect_events(price_obs(24000.0, TS1), None, None)
    assert isinstance(events, tuple)
    assert len(events) > 0  # OBSERVATION_CREATED + session extremes on the very first point
    assert all(e.event_type for e in events)


def test_none_current_returns_empty_and_unchanged_state():
    events, state = detect_events(None, None, None)
    assert events == ()
    assert state is None


def test_price_movement_generates_price_changed_event():
    _events1, state1 = detect_events(price_obs(24000.0, TS1), None, None)
    events2, _state2 = detect_events(price_obs(24200.0, TS2), price_obs(24000.0, TS1), state1)
    assert any(e.event_type == "PRICE_CHANGED" for e in events2)


def test_running_state_threads_correctly_no_false_new_low():
    # Regression test for the bug caught by this phase's own smoke test:
    # a rising price must not spuriously report NEW_SESSION_LOW just
    # because running_state was silently reset.
    _events1, state1 = detect_events(price_obs(24000.0, TS1), None, None)
    events2, state2 = detect_events(price_obs(24200.0, TS2), price_obs(24000.0, TS1), state1)
    assert not any(e.event_type == "NEW_SESSION_LOW" for e in events2)
    assert any(e.event_type == "NEW_SESSION_HIGH" for e in events2)
    assert state2.session_extremes.session_low == 24000.0  # correctly carried forward, not reset


def test_no_price_movement_produces_no_price_changed_event():
    _events1, state1 = detect_events(price_obs(24000.0, TS1), None, None)
    events2, _state2 = detect_events(price_obs(24000.0, TS2), price_obs(24000.0, TS1), state1)
    assert not any(e.event_type == "PRICE_CHANGED" for e in events2)
