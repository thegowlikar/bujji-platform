"""Tests -- Futures Adapter, Shadow Campaign v2 Phase 1. FakeBroker only."""
from __future__ import annotations

import pytest

from bujji.market_perception.futures_adapter import build_future_snapshot


class FakeBroker:
    def __init__(self, response=None, raises=False):
        self._response = response
        self._raises = raises

    async def get_futures_quote(self, underlying):
        if self._raises:
            raise RuntimeError("simulated broker failure")
        return self._response


@pytest.mark.asyncio
async def test_build_future_snapshot_maps_fields_and_computes_basis():
    broker = FakeBroker(response={"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24650.0, "volume": 12345.0, "oi": 987654.0})
    snap = await build_future_snapshot(broker, "NIFTY", spot=24600.0)
    assert snap.symbol == "NSE:NIFTY26AUGFUT"
    assert snap.ltp == 24650.0
    assert snap.volume == 12345.0
    assert snap.open_interest == 987654.0
    assert snap.basis == 50.0  # reuses futures_observation.engine.compute_basis: ltp - spot
    assert snap.premium_discount == 50.0


@pytest.mark.asyncio
async def test_build_future_snapshot_none_response_returns_none():
    broker = FakeBroker(response=None)
    assert await build_future_snapshot(broker, "NIFTY", spot=24600.0) is None


@pytest.mark.asyncio
async def test_build_future_snapshot_broker_exception_returns_none():
    broker = FakeBroker(raises=True)
    assert await build_future_snapshot(broker, "NIFTY", spot=24600.0) is None


@pytest.mark.asyncio
async def test_build_future_snapshot_missing_spot_leaves_basis_none():
    broker = FakeBroker(response={"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24650.0, "volume": None, "oi": None})
    snap = await build_future_snapshot(broker, "NIFTY", spot=None)
    assert snap.basis is None
    assert snap.premium_discount is None
