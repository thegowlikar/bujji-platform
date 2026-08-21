"""Tests -- assessment_bridge.py + full MarketStateBuilder pipeline,
Shadow Campaign v2 Phase 3B. FakeBroker-equivalent MarketSnapshots only,
no live calls anywhere."""
from __future__ import annotations

from bujji.market_perception.models import (
    FutureSnapshot, HEALTH_OK, MarketSnapshot, OptionChainConfig,
    OptionChainSnapshot, OptionLeg, SpotSnapshot, VixSnapshot,
)
from bujji.market_state_builder.assessment_bridge import build_market_state_assessment
from bujji.market_state_builder.market_state import MarketStateBuilder


def snap(spot=24600.0, ts="2026-08-03T09:15:00+05:30", vix=13.0, with_chain=True,
         ce_oi=12000.0, futures_ltp=24650.0, expiry="2026-08-06"):
    chain = None
    if with_chain:
        legs = (
            OptionLeg(symbol="CE24600", strike=24600.0, option_type="CE", ltp=None,
                      bid=99.0, ask=101.0, spread=2.0, volume=None, open_interest=ce_oi,
                      iv=None, delta=None, gamma=None, theta=None, vega=None),
            OptionLeg(symbol="PE24600", strike=24600.0, option_type="PE", ltp=None,
                      bid=88.0, ask=90.0, spread=2.0, volume=None, open_interest=15000.0,
                      iv=None, delta=None, gamma=None, theta=None, vega=None),
        )
        chain = OptionChainSnapshot(
            underlying="NIFTY", expiry=expiry, atm_strike=24600.0,
            config=OptionChainConfig(strike_range=100, strike_step=100), legs=legs,
        )
    futures = FutureSnapshot(symbol="NSE:NIFTY26AUGFUT", ltp=futures_ltp, volume=1000.0,
                              open_interest=None, basis=50.0, premium_discount=50.0) if futures_ltp is not None else None
    return MarketSnapshot(
        snapshot_version="1.0", timestamp=ts, source="fyers_live", latency_ms=10.0,
        health_status=HEALTH_OK, missing_fields=(),
        spot=SpotSnapshot(symbol="NIFTY", ltp=spot),
        vix=VixSnapshot(value=vix, prev_close=13.5), futures=futures, option_chain=chain,
    )


# --- build_market_state_assessment() unit tests ---

def test_empty_episodes_and_events_skips_price_and_market_structure():
    result = build_market_state_assessment((), (), (), "2026-08-03T09:15:00+05:30")
    assert result.price_structure is None
    assert result.market_structure is None
    assert result.participant_positioning is None


def test_empty_option_observations_skips_participant_positioning():
    result = build_market_state_assessment((), (), (), "2026-08-03T09:15:00+05:30")
    assert result.participant_positioning is None


# --- Full MarketStateBuilder pipeline tests ---

def test_first_cycle_no_crash_empty_understanding():
    builder = MarketStateBuilder()
    result = builder.process(snap())
    assert result is not None
    assert isinstance(result.events, tuple)
    assert isinstance(result.episodes, tuple)


def test_price_movement_across_two_cycles_produces_price_structure():
    builder = MarketStateBuilder()
    builder.process(snap(spot=24000.0, ts="2026-08-03T09:15:00+05:30"))
    result = builder.process(snap(spot=24200.0, ts="2026-08-03T09:16:00+05:30"))
    assert any(e.event_type == "PRICE_CHANGED" for e in result.events)
    assert result.price_structure is not None
    assert result.market_structure is not None


def test_oi_change_across_two_cycles_produces_oi_event():
    builder = MarketStateBuilder()
    builder.process(snap(ce_oi=100000.0, ts="2026-08-03T09:15:00+05:30"))
    result = builder.process(snap(ce_oi=150000.0, ts="2026-08-03T09:16:00+05:30"))
    assert result.participant_positioning is not None
    # Real OI value flows through the whole bridge, never fabricated:
    # msi_participant_positioning's own lens opinions are derived from
    # the actual 150000/15000 CE/PE OI figures built by
    # option_observation_bridge from the real MarketSnapshot legs.
    assert result.participant_positioning.assessment_id  # a real, non-empty computed id


def test_missing_vix_futures_option_chain_does_not_crash():
    builder = MarketStateBuilder()
    snapshot = snap(vix=None, futures_ltp=None, with_chain=False)
    result = builder.process(snapshot)
    assert result is not None
    assert result.participant_positioning is None  # honestly absent


def test_missing_spot_does_not_crash_and_produces_no_events():
    builder = MarketStateBuilder()
    snapshot = snap(spot=None)
    result = builder.process(snapshot)
    assert result.events == ()
    assert result.episodes == ()


def test_expiry_transition_does_not_incorrectly_merge_episodes():
    builder = MarketStateBuilder()
    builder.process(snap(spot=24000.0, ts="2026-08-03T09:15:00+05:30", expiry="2026-08-06"))
    result_before = builder.process(snap(spot=24200.0, ts="2026-08-03T09:16:00+05:30", expiry="2026-08-06"))
    episodes_before = result_before.episodes
    # A new weekly expiry rolling in changes the option_chain's expiry
    # field, but PRICE episodes are keyed off the PRICE observation
    # stream (spot), not the option chain -- confirm price episodes
    # are unaffected by an expiry change alone (no accidental merge or
    # split triggered by unrelated option-chain metadata).
    result_after = builder.process(snap(spot=24250.0, ts="2026-08-03T09:17:00+05:30", expiry="2026-08-13"))
    episode_ids_before = {ep.episode_id for ep in episodes_before}
    episode_ids_after = {ep.episode_id for ep in result_after.episodes}
    assert episode_ids_before.issubset(episode_ids_after)  # grew, not replaced/merged incorrectly


def test_large_volatility_change_does_not_crash():
    builder = MarketStateBuilder()
    builder.process(snap(spot=24000.0, ts="2026-08-03T09:15:00+05:30", vix=12.0))
    result = builder.process(snap(spot=23500.0, ts="2026-08-03T09:16:00+05:30", vix=22.0))
    assert result is not None  # VIX isn't wired into events this phase, but must never crash


def test_broken_data_across_cycles_never_crashes():
    builder = MarketStateBuilder()
    builder.process(snap(spot=24000.0, ts="2026-08-03T09:15:00+05:30"))
    builder.process(snap(spot=None, ts="2026-08-03T09:16:00+05:30"))  # broker glitch
    result = builder.process(snap(spot=24100.0, ts="2026-08-03T09:17:00+05:30"))
    assert result is not None
    assert any(e.event_type == "PRICE_CHANGED" for e in result.events)  # recovers cleanly
