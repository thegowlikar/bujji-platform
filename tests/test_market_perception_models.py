"""Tests -- Market Perception models, Shadow Campaign v2 Phase 1."""
from __future__ import annotations

import dataclasses

import pytest

from bujji.market_perception.models import (
    HEALTH_OK,
    FutureSnapshot,
    MarketSnapshot,
    OptionChainConfig,
    OptionChainSnapshot,
    OptionLeg,
    SpotSnapshot,
    VixSnapshot,
)


def make_snapshot(**overrides):
    defaults = dict(
        snapshot_version="1.0", timestamp="2026-08-03T11:00:00+05:30", source="fyers_live",
        latency_ms=42.0, health_status=HEALTH_OK, missing_fields=(),
        spot=SpotSnapshot(symbol="NIFTY", ltp=24600.0),
        vix=VixSnapshot(value=13.2, prev_close=13.5),
        futures=FutureSnapshot(symbol="NSE:NIFTY26AUGFUT", ltp=24650.0, volume=None, open_interest=None, basis=50.0, premium_discount=50.0),
        option_chain=None,
    )
    defaults.update(overrides)
    return MarketSnapshot(**defaults)


def test_creation():
    snap = make_snapshot()
    assert snap.spot.ltp == 24600.0
    assert snap.vix.value == 13.2
    assert snap.futures.basis == 50.0


def test_immutability():
    snap = make_snapshot()
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.timestamp = "changed"


def test_empty_timestamp_rejected():
    with pytest.raises(ValueError):
        make_snapshot(timestamp="")


def test_invalid_health_status_rejected():
    with pytest.raises(ValueError):
        make_snapshot(health_status="MAYBE")


def test_missing_fields_recorded_honestly():
    snap = make_snapshot(missing_fields=("futures", "option_chain"), health_status="DEGRADED")
    assert snap.missing_fields == ("futures", "option_chain")


def test_option_chain_config_defaults_and_validation():
    cfg = OptionChainConfig()
    assert cfg.strike_range == 2000
    assert cfg.strike_step == 100
    with pytest.raises(ValueError):
        OptionChainConfig(strike_range=0)
    with pytest.raises(ValueError):
        OptionChainConfig(strike_step=0)
    with pytest.raises(ValueError):
        OptionChainConfig(strike_range=150, strike_step=100)  # not an exact multiple


def test_option_leg_validation():
    leg = OptionLeg(
        symbol="NSE:NIFTY2680424600CE", strike=24600.0, option_type="CE",
        ltp=None, bid=99.0, ask=101.0, spread=2.0, volume=None, open_interest=125000.0,
        iv=None, delta=None, gamma=None, theta=None, vega=None,
    )
    assert leg.bid == 99.0
    with pytest.raises(ValueError):
        OptionLeg(
            symbol="", strike=24600.0, option_type="CE", ltp=None, bid=None, ask=None,
            spread=None, volume=None, open_interest=None, iv=None, delta=None, gamma=None,
            theta=None, vega=None,
        )
    with pytest.raises(ValueError):
        OptionLeg(
            symbol="X", strike=24600.0, option_type="XX", ltp=None, bid=None, ask=None,
            spread=None, volume=None, open_interest=None, iv=None, delta=None, gamma=None,
            theta=None, vega=None,
        )


def test_option_chain_snapshot_leg_lookup():
    cfg = OptionChainConfig(strike_range=200, strike_step=100)
    ce = OptionLeg(
        symbol="CE24600", strike=24600.0, option_type="CE", ltp=None, bid=99.0, ask=101.0,
        spread=2.0, volume=None, open_interest=None, iv=None, delta=None, gamma=None,
        theta=None, vega=None,
    )
    chain = OptionChainSnapshot(
        underlying="NIFTY", expiry="2026-08-06", atm_strike=24600.0, config=cfg, legs=(ce,),
    )
    assert chain.leg(24600.0, "CE") is ce
    assert chain.leg(24600.0, "PE") is None


def test_no_decision_or_signal_fields_anywhere():
    forbidden = {
        "signal", "decision", "confidence", "regime", "trend", "strategy", "trade",
        "position", "order", "risk", "approved", "blocked", "recommendation",
    }
    for cls in (MarketSnapshot, OptionLeg, OptionChainSnapshot, FutureSnapshot, SpotSnapshot, VixSnapshot):
        field_names = set(cls.__dataclass_fields__.keys())
        assert not (field_names & forbidden), f"{cls.__name__} has forbidden fields: {field_names & forbidden}"
