"""Tests -- MarketDataAdapter, Shadow Campaign v2 Phase 1. FakeBroker only,
no live FYERS calls, no instrument-master dependency (option chain is
monkeypatched out so this file tests orchestration/health status only --
option chain content itself is covered by
test_market_perception_option_chain_adapter.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bujji.market_perception import market_data_adapter as mda_module
from bujji.market_perception.market_data_adapter import MarketDataAdapter
from bujji.market_perception.models import HEALTH_DEGRADED, HEALTH_OK, HEALTH_UNAVAILABLE, OptionChainSnapshot, OptionChainConfig


class FakeClock:
    def __init__(self, start):
        self._t = start

    def __call__(self):
        t = self._t
        self._t = self._t + timedelta(milliseconds=5)
        return t


class FakeBroker:
    def __init__(self, spot=24600.0, vix=None, futures_quote=None, raise_spot=False):
        self._spot = spot
        self._vix = vix
        self._futures_quote = futures_quote
        self._raise_spot = raise_spot

    async def get_spot(self, underlying):
        if self._raise_spot:
            raise RuntimeError("simulated failure")
        return self._spot

    async def get_vix(self):
        return self._vix

    async def get_futures_quote(self, underlying):
        return self._futures_quote

    async def get_option_chain(self, underlying, spot, strike_count=5):
        return []

    async def get_quote(self, contract):
        return {"bid": 1.0, "ask": 2.0, "spread": 1.0}


@pytest.fixture(autouse=True)
def stub_option_chain(monkeypatch):
    async def _fake_build(broker, underlying, spot, config):
        return OptionChainSnapshot(
            underlying=underlying, expiry="2026-08-06", atm_strike=spot,
            config=config or OptionChainConfig(), legs=(),
        )
    monkeypatch.setattr(mda_module, "build_option_chain_snapshot", _fake_build)


@pytest.mark.asyncio
async def test_healthy_snapshot_when_everything_available():
    broker = FakeBroker(vix={"level": 13.0, "prev_close": 13.5}, futures_quote={"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24650.0, "volume": None, "oi": None})
    clock = FakeClock(datetime(2026, 8, 3, 11, 0, 0, tzinfo=timezone.utc))
    adapter = MarketDataAdapter(broker, clock)
    snap = await adapter.build_snapshot()

    assert snap.health_status == HEALTH_OK
    assert snap.missing_fields == ()
    assert snap.spot.ltp == 24600.0
    assert snap.vix.value == 13.0
    assert snap.futures.ltp == 24650.0
    assert snap.latency_ms > 0


@pytest.mark.asyncio
async def test_degraded_when_vix_missing():
    broker = FakeBroker(vix=None, futures_quote={"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24650.0, "volume": None, "oi": None})
    clock = FakeClock(datetime(2026, 8, 3, 11, 0, 0, tzinfo=timezone.utc))
    adapter = MarketDataAdapter(broker, clock)
    snap = await adapter.build_snapshot()

    assert snap.health_status == HEALTH_DEGRADED
    assert "vix" in snap.missing_fields


@pytest.mark.asyncio
async def test_unavailable_when_spot_fails():
    broker = FakeBroker(raise_spot=True)
    clock = FakeClock(datetime(2026, 8, 3, 11, 0, 0, tzinfo=timezone.utc))
    adapter = MarketDataAdapter(broker, clock)
    snap = await adapter.build_snapshot()

    assert snap.health_status == HEALTH_UNAVAILABLE
    assert snap.spot.ltp is None
    assert "spot" in snap.missing_fields
    # Option chain can't be resolved without a spot value.
    assert "option_chain" in snap.missing_fields
