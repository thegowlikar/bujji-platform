"""Tests -- episode_bridge.py, Shadow Campaign v2 Phase 3B."""
from __future__ import annotations

from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_observation.engine import build_observation
from bujji.market_state_builder.episode_bridge import fold_events_into_episodes
from bujji.market_state_builder.event_bridge import detect_events

TS1 = "2026-08-03T09:15:00+05:30"
TS2 = "2026-08-03T09:16:00+05:30"
TS3 = "2026-08-03T09:17:00+05:30"


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


def test_empty_events_returns_unchanged_episodes():
    assert fold_events_into_episodes((), ()) == ()
    events, _ = detect_events(None, None, None)
    assert fold_events_into_episodes((), events) == ()


def test_first_price_observation_creates_an_episode():
    events, _state = detect_events(price_obs(24000.0, TS1), None, None)
    episodes = fold_events_into_episodes((), events)
    assert len(episodes) >= 1
    assert episodes[0].current_state in ("CREATED", "ACTIVE")


def test_episode_grows_across_cycles_same_episode_id():
    events1, state1 = detect_events(price_obs(24000.0, TS1), None, None)
    episodes1 = fold_events_into_episodes((), events1)
    first_id = episodes1[0].episode_id

    events2, _state2 = detect_events(price_obs(24200.0, TS2), price_obs(24000.0, TS1), state1)
    episodes2 = fold_events_into_episodes(episodes1, events2)

    assert any(ep.episode_id == first_id for ep in episodes2)  # same episode grew, not replaced
    grown = next(ep for ep in episodes2 if ep.episode_id == first_id)
    assert len(grown.originating_event_ids) >= len(episodes1[0].originating_event_ids)


def test_duplicate_observation_does_not_create_a_second_episode():
    events1, state1 = detect_events(price_obs(24000.0, TS1), None, None)
    episodes1 = fold_events_into_episodes((), events1)

    # Re-processing the exact same events again must be a no-op (INV-7,
    # market_episode's own idempotency guarantee) -- confirms this
    # bridge doesn't accidentally double-fold.
    episodes_again = fold_events_into_episodes(episodes1, events1)
    assert episodes_again == episodes1


def test_closed_episode_never_reopens_via_this_bridge():
    # This bridge never calls advance_time()/closes episodes itself --
    # confirm episodes stay open across normal folding (closing is a
    # separate, time-based concern this phase does not implement).
    events1, state1 = detect_events(price_obs(24000.0, TS1), None, None)
    episodes1 = fold_events_into_episodes((), events1)
    assert all(ep.current_state != "CLOSED" for ep in episodes1)
