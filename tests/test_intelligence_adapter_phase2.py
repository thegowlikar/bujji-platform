"""Tests -- Intelligence Snapshot Adapter, Shadow Campaign v2 Phase 2.
FakeBroker only -- no real credentials, no network."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bujji.core.models import Candle
from bujji.market_perception.intelligence_adapter import (
    build_intelligence_snapshot,
    fetch_spot_candles,
)
from bujji.market_perception.models import (
    FutureSnapshot, MarketSnapshot, OptionChainConfig, OptionChainSnapshot,
    OptionLeg, SpotSnapshot, VixSnapshot, HEALTH_OK,
)


def make_candles(n=10, start_close=24500.0):
    out = []
    close = start_close
    for i in range(n):
        close += 5.0
        out.append(Candle(timestamp=datetime(2026, 8, 3, 9, i, tzinfo=timezone.utc),
                           open=close - 5, high=close + 2, low=close - 7, close=close, volume=1000))
    return out


def make_snapshot(with_chain=True, vix=13.0, vix_prev=13.5):
    legs = ()
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
    return MarketSnapshot(
        snapshot_version="1.0", timestamp="2026-08-03T11:00:00+05:30", source="fyers_live",
        latency_ms=10.0, health_status=HEALTH_OK, missing_fields=(),
        spot=SpotSnapshot(symbol="NIFTY", ltp=24600.0),
        vix=VixSnapshot(value=vix, prev_close=vix_prev),
        futures=FutureSnapshot(symbol="NSE:NIFTY26AUGFUT", ltp=24650.0, volume=1000.0, open_interest=None, basis=50.0, premium_discount=50.0),
        option_chain=chain,
    )


class FakeBroker:
    def __init__(self, candles=None, raises=False):
        self._candles = candles if candles is not None else make_candles()
        self._raises = raises

    async def get_recent_candles(self, underlying, minutes, count):
        if self._raises:
            raise RuntimeError("simulated candle fetch failure")
        return self._candles


@pytest.mark.asyncio
async def test_fetch_spot_candles_returns_real_candles():
    broker = FakeBroker()
    candles = await fetch_spot_candles(broker, "NIFTY")
    assert len(candles) == 10
    assert isinstance(candles[0], Candle)


@pytest.mark.asyncio
async def test_fetch_spot_candles_degrades_to_empty_on_broker_failure():
    broker = FakeBroker(raises=True)
    candles = await fetch_spot_candles(broker, "NIFTY")
    assert candles == []


def test_build_intelligence_snapshot_returns_regime_liquidity_structure_event_behaviour():
    snap = make_snapshot()
    candles = make_candles()
    now = datetime(2026, 8, 3, 11, 0, 0, tzinfo=timezone.utc)
    result = build_intelligence_snapshot(snap, candles, now)

    for key in ("regime", "liquidity", "structure", "event", "behaviour"):
        assert key in result, f"expected {key!r} present in intelligence result"


def test_build_intelligence_snapshot_omits_position_dependent_brains_without_a_position():
    # Documented, honest structural limitation: no Virtual Portfolio in
    # Phase 2 -> position=None -> volatility/premium/greeks are simply
    # absent, never fabricated placeholders.
    snap = make_snapshot()
    candles = make_candles()
    now = datetime(2026, 8, 3, 11, 0, 0, tzinfo=timezone.utc)
    result = build_intelligence_snapshot(snap, candles, now)

    for key in ("volatility", "premium", "greeks"):
        assert key not in result


def test_build_intelligence_snapshot_no_option_chain_still_runs_safely():
    snap = make_snapshot(with_chain=False)
    candles = make_candles()
    now = datetime(2026, 8, 3, 11, 0, 0, tzinfo=timezone.utc)
    result = build_intelligence_snapshot(snap, candles, now)
    assert "structure" in result
    assert "liquidity" in result


def test_build_intelligence_snapshot_empty_candles_does_not_raise():
    snap = make_snapshot()
    now = datetime(2026, 8, 3, 11, 0, 0, tzinfo=timezone.utc)
    result = build_intelligence_snapshot(snap, [], now)
    assert "regime" in result  # RegimeBrain is null-safe on empty input, per its own design
