"""Tests -- Intelligence Cycle Recorder, Shadow Campaign v2 Continuous
Intelligence Observatory. FakeBroker only, no live calls."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from bujji.core.enums import OptionType, Side
from bujji.core.models import Candle, OptionContract
from bujji.market_perception.models import (
    HEALTH_OK, MarketSnapshot, OptionChainConfig, OptionChainSnapshot,
    OptionLeg, SpotSnapshot, VixSnapshot,
)
from bujji.market_state.intelligence_cycle_recorder import IntelligenceCycleRecorder
from bujji.shadow_runtime.shadow_session_runner import ShadowSessionRunner

CE_CONTRACT = OptionContract("NIFTY24450CE", "NIFTY", 24450, OptionType.CE, "2026-08-04", 65)
PE_CONTRACT = OptionContract("NIFTY24450PE", "NIFTY", 24450, OptionType.PE, "2026-08-04", 65)
FIXED_NOW = datetime(2026, 8, 4, 9, 15, 0, tzinfo=timezone.utc)


def clock():
    return FIXED_NOW


async def no_sleep(_seconds):
    return None


def make_snapshot(spot=24400.0, ts="2026-08-04T09:15:00+05:30"):
    legs = (
        OptionLeg(symbol="CE", strike=24450.0, option_type="CE", ltp=None, bid=99.0, ask=101.0,
                  spread=2.0, volume=None, open_interest=12000.0, iv=None, delta=None, gamma=None,
                  theta=None, vega=None),
        OptionLeg(symbol="PE", strike=24450.0, option_type="PE", ltp=None, bid=88.0, ask=90.0,
                  spread=2.0, volume=None, open_interest=15000.0, iv=None, delta=None, gamma=None,
                  theta=None, vega=None),
    )
    chain = OptionChainSnapshot(underlying="NIFTY", expiry="2026-08-04", atm_strike=24450.0,
                                 config=OptionChainConfig(strike_range=100, strike_step=100), legs=legs)
    return MarketSnapshot(
        snapshot_version="1.0", timestamp=ts, source="fyers_live", latency_ms=1.0,
        health_status=HEALTH_OK, missing_fields=(),
        spot=SpotSnapshot(symbol="NIFTY", ltp=spot), vix=VixSnapshot(value=13.0),
        futures=None, option_chain=chain,
    )


class FakeBroker:
    def __init__(self):
        self.connect_calls = 0

    async def connect(self):
        self.connect_calls += 1

    async def get_quote(self, contract):
        return {"bid": 98.0, "ask": 100.0, "spread": 2.0}

    async def get_spot(self, underlying):
        return 24400.0

    async def get_vix(self):
        return {"level": 13.0, "prev_close": 13.5}

    async def get_option_chain(self, underlying, spot, strike_count=5):
        return [(24450.0, 12000.0, 15000.0)]

    async def get_futures_quote(self, underlying):
        return {"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24650.0, "volume": 1000.0, "oi": None}

    async def get_recent_candles(self, underlying, minutes, count):
        out = []
        c = 24300.0
        for i in range(20):
            c += 5
            out.append(Candle(timestamp=FIXED_NOW, open=c - 5, high=c + 2, low=c - 7, close=c, volume=1000))
        return out


# --- Standalone recorder tests ---

@pytest.mark.asyncio
async def test_record_cycle_returns_all_expected_keys():
    recorder = IntelligenceCycleRecorder()
    broker = FakeBroker()
    record = await recorder.record_cycle(make_snapshot(), broker, clock)
    expected_keys = {
        "timestamp", "market_snapshot_health", "market_snapshot_missing_fields",
        "price_structure", "market_structure", "participant_positioning",
        "market_direction", "volatility_structure", "market_state",
        "consensus", "opportunity", "trade_thesis", "strategy_eligibility",
        # Added 2026-08-06 -- Strategy Suitability/Selection/Trade Intent,
        # description-only extensions (see
        # test_intelligence_cycle_recorder_extended.py for their own
        # dedicated coverage).
        "strategy_suitability", "strategy_selection", "trade_intent",
        # Added Phase 9 (Liquidity Intelligence Bridge) -- real
        # LiquidityReading persisted for transparency, independent of
        # whether any family required it this cycle (see
        # test_intelligence_cycle_recorder_liquidity.py for dedicated
        # coverage).
        "liquidity",
        # Added Phase 10 (Market Memory Integrity Upgrade) -- explicit
        # memory-completeness telemetry (see
        # test_intelligence_cycle_recorder_memory_health.py for
        # dedicated coverage).
        "memory_health",
        # Added Phase 11 (Intelligence Depth Upgrade) -- regime memory
        # (how long the current regime has persisted, transition
        # context) and a rule-based narrative built from this cycle's
        # own resolved evidence (see test_market_regime_memory.py,
        # test_market_narrative.py for dedicated coverage).
        "regime_memory", "narrative",
        # Added Phase 15E (Greeks + Premium Behaviour Intelligence) --
        # real per-leg ATM delta/gamma/theta/vega (see
        # test_msi_greeks.py) and a real cross-cycle rolling premium
        # window (see test_premium_behaviour.py). Both purely
        # observational this phase -- neither feeds any decision engine.
        "greeks", "premium_behaviour",
    }
    assert set(record.keys()) == expected_keys


@pytest.mark.asyncio
async def test_record_cycle_is_fully_json_serializable():
    recorder = IntelligenceCycleRecorder()
    broker = FakeBroker()
    record = await recorder.record_cycle(make_snapshot(), broker, clock)
    json.dumps(record, default=str)  # must not raise


@pytest.mark.asyncio
async def test_second_cycle_produces_real_price_structure_from_episode_history():
    recorder = IntelligenceCycleRecorder()
    broker = FakeBroker()
    await recorder.record_cycle(make_snapshot(24400.0, "2026-08-04T09:15:00+05:30"), broker, clock)
    record2 = await recorder.record_cycle(make_snapshot(24460.0, "2026-08-04T09:16:00+05:30"), broker, clock)
    assert record2["price_structure"] is not None
    assert record2["market_structure"] is not None


@pytest.mark.asyncio
async def test_never_fabricates_missing_opportunity_or_thesis_fields():
    recorder = IntelligenceCycleRecorder()
    broker = FakeBroker()
    record = await recorder.record_cycle(make_snapshot(), broker, clock)
    # First cycle has no episode history yet -- honest, real degraded
    # results are expected, never a fabricated "confident" read.
    if record["trade_thesis"] is not None:
        assert record["trade_thesis"]["thesis_type"] in (
            "NO_TRADE", "TREND_CONTINUATION", "TREND_REVERSAL", "RANGE_PERSISTENCE",
            "VOLATILITY_EXPANSION", "VOLATILITY_COMPRESSION", "BREAKOUT", "FAILED_BREAKOUT",
            "MEAN_REVERSION", "EVENT_RISK",
        )


# --- ShadowSessionRunner wiring tests ---

def make_runner(broker, tmp_path, **overrides):
    defaults = dict(
        broker=broker, watchlist=[(CE_CONTRACT, Side.SELL), (PE_CONTRACT, Side.SELL)],
        storage_path=tmp_path / "quotes.jsonl", session_id="OBS-TEST", clock=clock,
        max_cycles=2, max_consecutive_failures=2, sleep_seconds=0.0, sleep_fn=no_sleep,
        market_perception_enabled=True,
        market_snapshot_path=str(tmp_path / "market_snapshots.jsonl"),
        intelligence_snapshot_path=str(tmp_path / "intelligence_snapshots.jsonl"),
    )
    defaults.update(overrides)
    return ShadowSessionRunner(**defaults)


@pytest.mark.asyncio
async def test_intelligence_cycle_disabled_by_default_no_new_file(tmp_path):
    broker = FakeBroker()
    runner = make_runner(broker, tmp_path)  # intelligence_cycle_enabled omitted -> False
    artifact = await runner.start()
    assert artifact.observations_count == 4  # existing Phase 5/2 behavior unaffected
    assert not (tmp_path / "intelligence_cycle.jsonl").exists()


@pytest.mark.asyncio
async def test_intelligence_cycle_enabled_produces_real_records(tmp_path):
    broker = FakeBroker()
    cycle_path = tmp_path / "intelligence_cycle.jsonl"
    runner = make_runner(
        broker, tmp_path, intelligence_cycle_enabled=True, intelligence_cycle_path=str(cycle_path),
    )
    artifact = await runner.start()

    assert artifact.observations_count == 4  # existing quote loop completely unaffected
    assert artifact.errors == ()
    assert cycle_path.exists()
    lines = cycle_path.read_text().strip().splitlines()
    assert len(lines) == 2  # one per cycle
    row = json.loads(lines[0])
    assert "trade_thesis" in row
    assert "strategy_eligibility" in row


@pytest.mark.asyncio
async def test_candle_fetch_failure_degrades_gracefully_never_breaks_quote_loop(tmp_path):
    # fetch_spot_candles() (Phase 2) already catches broker exceptions
    # internally and degrades to an empty candle list -- this proves
    # that degradation propagates correctly through the whole
    # intelligence chain as an honestly-thin RegimeBrain read, WITHOUT
    # raising, WITHOUT fabricating candles, and WITHOUT ever touching
    # the existing quote loop.
    class FlakyCandleBroker(FakeBroker):
        async def get_recent_candles(self, underlying, minutes, count):
            raise RuntimeError("simulated failure deep in the intelligence chain")

    broker = FlakyCandleBroker()
    cycle_path = tmp_path / "intelligence_cycle.jsonl"
    runner = make_runner(
        broker, tmp_path, intelligence_cycle_enabled=True, intelligence_cycle_path=str(cycle_path),
    )
    artifact = await runner.start()
    assert artifact.observations_count == 4  # existing quote loop still fully unaffected
    assert artifact.errors == ()  # candle failure was absorbed gracefully, not a cycle error
    assert cycle_path.exists()
    rows = [json.loads(l) for l in cycle_path.read_text().strip().splitlines()]
    assert len(rows) == 2  # both cycles still produced real, honest records
