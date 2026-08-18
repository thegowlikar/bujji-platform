"""Tests -- MSI Intelligence Bridge, Shadow Campaign v2 Phase 3A.
No broker, no network, no real credentials -- fake MarketSnapshots and
fake Candle lists only."""
from __future__ import annotations

from datetime import datetime, timezone

from bujji.core.models import Candle
from bujji.market_perception.models import (
    FutureSnapshot, HEALTH_OK, MarketSnapshot, OptionChainConfig,
    OptionChainSnapshot, OptionLeg, SpotSnapshot, VixSnapshot,
)
from bujji.market_perception.msi_adapter import (
    build_volatility_structure_assessment,
    closes_with_timestamps,
)


def make_candles(n=8, start_close=24500.0):
    out = []
    close = start_close
    for i in range(n):
        close += 5.0
        out.append(Candle(timestamp=datetime(2026, 8, 3, 9, i, tzinfo=timezone.utc),
                           open=close - 5, high=close + 2, low=close - 7, close=close, volume=1000))
    return out


def make_snapshot(with_chain=True, spot=24600.0, expiry="2026-08-06", ce_bid=99.0, ce_ask=101.0, pe_bid=88.0, pe_ask=90.0):
    chain = None
    if with_chain:
        legs = (
            OptionLeg(symbol="CE24600", strike=24600.0, option_type="CE", ltp=None,
                      bid=ce_bid, ask=ce_ask, spread=2.0, volume=None, open_interest=12000.0,
                      iv=None, delta=None, gamma=None, theta=None, vega=None),
            OptionLeg(symbol="PE24600", strike=24600.0, option_type="PE", ltp=None,
                      bid=pe_bid, ask=pe_ask, spread=2.0, volume=None, open_interest=15000.0,
                      iv=None, delta=None, gamma=None, theta=None, vega=None),
        )
        chain = OptionChainSnapshot(
            underlying="NIFTY", expiry=expiry, atm_strike=24600.0,
            config=OptionChainConfig(strike_range=100, strike_step=100), legs=legs,
        )
    return MarketSnapshot(
        snapshot_version="1.0", timestamp="2026-08-03T11:00:00+05:30", source="fyers_live",
        latency_ms=10.0, health_status=HEALTH_OK, missing_fields=(),
        spot=SpotSnapshot(symbol="NIFTY", ltp=spot),
        vix=VixSnapshot(value=13.0, prev_close=13.5),
        futures=FutureSnapshot(symbol="NSE:NIFTY26AUGFUT", ltp=24650.0, volume=None, open_interest=None, basis=50.0, premium_discount=50.0),
        option_chain=chain,
    )


NOW = datetime(2026, 8, 3, 11, 0, 0, tzinfo=timezone.utc)


def test_closes_with_timestamps_maps_real_candles():
    candles = make_candles(3)
    pairs = closes_with_timestamps(candles)
    assert len(pairs) == 3
    assert pairs[0][1] == candles[0].close
    assert pairs[0][0] == candles[0].timestamp.isoformat()


def test_build_assessment_with_full_real_data():
    snap = make_snapshot()
    candles = make_candles()
    result = build_volatility_structure_assessment(snap, candles, NOW)
    assert result is not None
    assert result.timestamp == snap.timestamp
    assert result.iv_average is not None  # both ATM premiums present -> real IV solved


def test_returns_none_when_spot_missing():
    snap = make_snapshot(spot=None)
    result = build_volatility_structure_assessment(snap, make_candles(), NOW)
    assert result is None


def test_returns_none_when_option_chain_missing():
    snap = make_snapshot(with_chain=False)
    result = build_volatility_structure_assessment(snap, make_candles(), NOW)
    assert result is None


def test_returns_none_when_expiry_already_past():
    snap = make_snapshot(expiry="2026-07-01")  # before NOW
    result = build_volatility_structure_assessment(snap, make_candles(), NOW)
    assert result is None


def test_returns_none_when_expiry_invalid():
    snap = make_snapshot(expiry="not-a-date")
    result = build_volatility_structure_assessment(snap, make_candles(), NOW)
    assert result is None


def test_missing_premiums_still_produces_honest_assessment_not_fabricated():
    # One-sided bid/ask -> mid premium correctly withheld (None), not
    # approximated -- assess_volatility_structure's own null-safe gate
    # then reports iv_average=None honestly, not a guessed number.
    snap = make_snapshot(ce_bid=None, ce_ask=None)
    result = build_volatility_structure_assessment(snap, make_candles(), NOW)
    assert result is not None
    assert result.iv_average is None


def test_empty_candles_still_returns_an_assessment():
    snap = make_snapshot()
    result = build_volatility_structure_assessment(snap, [], NOW)
    assert result is not None
    assert result.realized_vol is None  # honestly absent, not fabricated
