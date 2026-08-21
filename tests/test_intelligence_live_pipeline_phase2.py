"""Tests -- Live Pipeline Integration, Shadow Campaign v2 Phase 2.

Exercises ShadowSessionRunner with market_perception_enabled=True end
to end against a FakeBroker, confirming: (a) existing quote logging is
completely unaffected, (b) market_snapshots.jsonl and
intelligence_snapshots.jsonl are correctly produced, (c) the flag
defaults to False and changes nothing when omitted (Phase 1 behavior
preserved exactly). No real credentials, no network, anywhere.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from bujji.core.enums import OptionType, Side
from bujji.core.models import Candle, OptionContract
from bujji.execution_reality.quote_observation_store import QuoteObservationStore
from bujji.shadow_runtime.shadow_session_runner import ShadowSessionRunner

CE_CONTRACT = OptionContract("NIFTY25000CE", "NIFTY", 25000, OptionType.CE, "2026-08-06", 75)
PE_CONTRACT = OptionContract("NIFTY25000PE", "NIFTY", 25000, OptionType.PE, "2026-08-06", 75)
FIXED_NOW = datetime(2026, 8, 3, 9, 15, 0, tzinfo=timezone.utc)


def clock():
    return FIXED_NOW


async def no_sleep(_seconds):
    return None


class FakeFullBroker:
    """Implements every read-only method market_perception needs, plus
    the pre-existing connect()/get_quote() Phase-5 needs. No
    order/position/margin method exists on this class at all."""

    def __init__(self):
        self.connect_calls = 0
        self.futures_calls = 0
        self.candle_calls = 0
        self.option_chain_calls = 0
        self.vix_calls = 0
        self.spot_calls = 0

    async def connect(self):
        self.connect_calls += 1

    async def get_quote(self, contract):
        return {"bid": 98.0, "ask": 100.0, "spread": 2.0}

    async def get_spot(self, underlying):
        self.spot_calls += 1
        return 24600.0

    async def get_vix(self):
        self.vix_calls += 1
        return {"level": 13.0, "prev_close": 13.5}

    async def get_option_chain(self, underlying, spot, strike_count=5):
        self.option_chain_calls += 1
        return [(24600.0, 12000.0, 15000.0)]

    async def get_futures_quote(self, underlying):
        self.futures_calls += 1
        return {"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24650.0, "volume": 1000.0, "oi": None}

    async def get_recent_candles(self, underlying, minutes, count):
        self.candle_calls += 1
        return [
            Candle(timestamp=FIXED_NOW, open=24590, high=24610, low=24580, close=24600 + i, volume=1000)
            for i in range(5)
        ]


@pytest.fixture(autouse=True)
def stub_option_chain_adapter(monkeypatch):
    # Avoid a real instrument-master CSV dependency in this integration
    # test -- OptionChainConfig/adapter unit behavior is already covered
    # by tests/test_market_perception_option_chain_adapter.py.
    from bujji.market_perception import market_data_adapter as mda_module
    from bujji.market_perception.models import OptionChainSnapshot, OptionChainConfig

    async def _fake_build(broker, underlying, spot, config):
        return OptionChainSnapshot(underlying=underlying, expiry="2026-08-06", atm_strike=spot, config=config, legs=())

    monkeypatch.setattr(mda_module, "build_option_chain_snapshot", _fake_build)


def make_runner(broker, tmp_path, **overrides):
    defaults = dict(
        broker=broker, watchlist=[(CE_CONTRACT, Side.SELL), (PE_CONTRACT, Side.SELL)],
        storage_path=tmp_path / "quotes.jsonl", session_id="PHASE2-TEST", clock=clock,
        max_cycles=2, max_consecutive_failures=2, sleep_seconds=0.0, sleep_fn=no_sleep,
    )
    defaults.update(overrides)
    return ShadowSessionRunner(**defaults)


@pytest.mark.asyncio
async def test_market_perception_disabled_by_default_no_new_broker_calls(tmp_path):
    broker = FakeFullBroker()
    runner = make_runner(broker, tmp_path)  # market_perception_enabled omitted -> False
    artifact = await runner.start()

    assert artifact.observations_count == 4  # unchanged Phase-5 behavior: 2 contracts x 2 cycles
    assert broker.spot_calls == 0
    assert broker.vix_calls == 0
    assert broker.futures_calls == 0
    assert broker.candle_calls == 0


@pytest.mark.asyncio
async def test_market_perception_enabled_produces_both_artifacts(tmp_path):
    broker = FakeFullBroker()
    market_path = tmp_path / "market_snapshots.jsonl"
    intel_path = tmp_path / "intelligence_snapshots.jsonl"
    runner = make_runner(
        broker, tmp_path, market_perception_enabled=True,
        market_snapshot_path=str(market_path), intelligence_snapshot_path=str(intel_path),
    )
    artifact = await runner.start()

    # Existing quote observation behavior is completely unaffected.
    assert artifact.observations_count == 4
    assert artifact.errors == ()

    assert market_path.exists()
    assert intel_path.exists()
    market_lines = market_path.read_text().strip().splitlines()
    intel_lines = intel_path.read_text().strip().splitlines()
    assert len(market_lines) == 2  # one per cycle
    assert len(intel_lines) == 2

    market_row = json.loads(market_lines[0])
    assert market_row["spot"]["ltp"] == 24600.0
    assert market_row["vix"]["value"] == 13.0

    intel_row = json.loads(intel_lines[0])
    assert "regime" in intel_row["intelligence"]
    assert "liquidity" in intel_row["intelligence"]
    # Documented Phase 2 limitation: no Virtual Portfolio -> no position.
    assert "greeks" not in intel_row["intelligence"]

    assert broker.spot_calls == 2
    assert broker.vix_calls == 2
    assert broker.futures_calls == 2
    assert broker.candle_calls == 2


@pytest.mark.asyncio
async def test_market_perception_partial_failure_recorded_honestly_not_fatal(tmp_path):
    # MarketDataAdapter.build_snapshot() itself already degrades gracefully
    # on a single failed source (per its own Phase 1 design) -- this proves
    # that degradation surfaces correctly through the runner as an honest
    # HEALTH_UNAVAILABLE snapshot, WITHOUT ever crashing the existing quote
    # loop or fabricating a fake spot value.
    class FlakyBroker(FakeFullBroker):
        async def get_spot(self, underlying):
            raise RuntimeError("simulated market_perception failure")

    broker = FlakyBroker()
    market_path = tmp_path / "market_snapshots.jsonl"
    runner = make_runner(
        broker, tmp_path, market_perception_enabled=True,
        market_snapshot_path=str(market_path),
        intelligence_snapshot_path=str(tmp_path / "intelligence_snapshots.jsonl"),
    )
    artifact = await runner.start()

    # The existing quote loop must still complete normally, unaffected.
    assert artifact.observations_count == 4
    assert artifact.errors == ()

    market_row = json.loads(market_path.read_text().strip().splitlines()[0])
    assert market_row["health_status"] == "UNAVAILABLE"
    assert "spot" in market_row["missing_fields"]
    assert market_row["spot"]["ltp"] is None  # never fabricated


@pytest.mark.asyncio
async def test_quote_observation_store_unaffected_by_market_perception(tmp_path):
    broker = FakeFullBroker()
    storage_path = tmp_path / "quotes.jsonl"
    runner = make_runner(
        broker, tmp_path, storage_path=storage_path, market_perception_enabled=True,
        market_snapshot_path=str(tmp_path / "market_snapshots.jsonl"),
        intelligence_snapshot_path=str(tmp_path / "intelligence_snapshots.jsonl"),
    )
    await runner.start()

    store = QuoteObservationStore(storage_path)
    rows = store.read_all()
    assert len(rows) == 4
    assert rows[0]["normalized_result"]["data_quality"] == "LIVE_QUOTE"
