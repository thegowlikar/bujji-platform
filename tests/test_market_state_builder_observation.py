"""Tests -- observation_bridge.py / option_observation_bridge.py,
Shadow Campaign v2 Phase 3B. No broker, no network."""
from __future__ import annotations

from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_perception.models import (
    FutureSnapshot, HEALTH_OK, MarketSnapshot, OptionChainConfig,
    OptionChainSnapshot, OptionLeg, SpotSnapshot, VixSnapshot,
)
from bujji.market_state_builder.observation_bridge import (
    build_futures_observation_from_snapshot,
    build_observation_from_snapshot,
    build_vix_observation_from_snapshot,
)
from bujji.market_state_builder.option_observation_bridge import (
    build_option_observations_from_snapshot,
)

TS = "2026-08-03T11:00:00+05:30"


def make_snapshot(spot=24600.0, vix=13.0, futures_ltp=24650.0, with_chain=True):
    chain = None
    if with_chain:
        legs = (
            OptionLeg(symbol="CE24600", strike=24600.0, option_type="CE", ltp=None,
                      bid=99.0, ask=101.0, spread=2.0, volume=None, open_interest=12000.0,
                      iv=None, delta=None, gamma=None, theta=None, vega=None),
            OptionLeg(symbol="PE24600", strike=24600.0, option_type="PE", ltp=None,
                      bid=88.0, ask=90.0, spread=2.0, volume=None, open_interest=15000.0,
                      iv=None, delta=None, gamma=None, theta=None, vega=None),
        )
        chain = OptionChainSnapshot(
            underlying="NIFTY", expiry="2026-08-06", atm_strike=24600.0,
            config=OptionChainConfig(strike_range=100, strike_step=100), legs=legs,
        )
    futures = FutureSnapshot(symbol="NSE:NIFTY26AUGFUT", ltp=futures_ltp, volume=1000.0,
                              open_interest=None, basis=50.0, premium_discount=50.0) if futures_ltp is not None else None
    return MarketSnapshot(
        snapshot_version="1.0", timestamp=TS, source="fyers_live", latency_ms=10.0,
        health_status=HEALTH_OK, missing_fields=(),
        spot=SpotSnapshot(symbol="NIFTY", ltp=spot),
        vix=VixSnapshot(value=vix, prev_close=13.5),
        futures=futures, option_chain=chain,
    )


def test_build_observation_from_snapshot_real_price():
    snap = make_snapshot()
    obs = build_observation_from_snapshot(snap)
    assert obs is not None
    assert obs.identity.observation_type == moc_taxonomy.TYPE_PRICE
    assert obs.identity.instrument == "NIFTY"
    assert obs.value.payload == 24600.0


def test_build_observation_from_snapshot_missing_spot_returns_none():
    snap = make_snapshot(spot=None)
    assert build_observation_from_snapshot(snap) is None


def test_build_vix_observation_missing_returns_none():
    snap = make_snapshot(vix=None)
    assert build_vix_observation_from_snapshot(snap) is None


def test_build_vix_observation_real():
    snap = make_snapshot()
    obs = build_vix_observation_from_snapshot(snap)
    assert obs.identity.observation_type == moc_taxonomy.TYPE_VOLATILITY_VIX
    assert obs.value.payload == 13.0


def test_build_futures_observation_missing_returns_none():
    snap = make_snapshot(futures_ltp=None)
    assert build_futures_observation_from_snapshot(snap) is None


def test_build_futures_observation_partial_data_discloses_missing_fields():
    snap = make_snapshot()
    obs = build_futures_observation_from_snapshot(snap)
    assert obs is not None
    assert obs.value.payload["close"] == 24650.0
    assert "open_interest" in obs.quality.missing_fields  # never fabricated


def test_build_option_observations_empty_when_no_chain():
    snap = make_snapshot(with_chain=False)
    assert build_option_observations_from_snapshot(snap) == ()


def test_build_option_observations_preserves_strike_type_oi_underlying_price():
    snap = make_snapshot()
    obs_tuple = build_option_observations_from_snapshot(snap)
    assert len(obs_tuple) == 2
    ce = next(o for o in obs_tuple if o.option_type == "CE")
    assert ce.strike == 24600.0
    assert ce.open_interest == 12000.0
    assert ce.underlying_price == 24600.0
    assert ce.expiry == "2026-08-06"
    assert ce.timestamp == TS


def test_build_option_observations_no_invented_greeks():
    # OptionObservation has no iv/delta/gamma/theta/vega fields at all --
    # confirm the wrapped Observation payload never carries them.
    snap = make_snapshot()
    obs_tuple = build_option_observations_from_snapshot(snap)
    for obs in obs_tuple:
        assert "iv" not in obs.payload
        assert "delta" not in obs.payload
