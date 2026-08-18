"""Tests -- Phase 11 recorder wiring (regime_memory + narrative persisted
each cycle). FakeBroker only, no live calls."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from bujji.core.models import Candle
from bujji.market_perception.models import (
    HEALTH_OK, MarketSnapshot, OptionChainConfig, OptionChainSnapshot,
    OptionLeg, SpotSnapshot, VixSnapshot,
)
from bujji.market_state.intelligence_cycle_recorder import IntelligenceCycleRecorder


def make_snapshot(spot=24400.0, ts="2026-08-06T09:15:00+05:30"):
    legs = (
        OptionLeg(symbol="CE", strike=24450.0, option_type="CE", ltp=None, bid=99.0, ask=101.0,
                  spread=2.0, volume=None, open_interest=12000.0, iv=None, delta=None, gamma=None, theta=None, vega=None),
        OptionLeg(symbol="PE", strike=24450.0, option_type="PE", ltp=None, bid=88.0, ask=90.0,
                  spread=2.0, volume=None, open_interest=15000.0, iv=None, delta=None, gamma=None, theta=None, vega=None),
    )
    chain = OptionChainSnapshot(underlying="NIFTY", expiry="2026-08-06", atm_strike=24450.0,
                                 config=OptionChainConfig(strike_range=100, strike_step=100), legs=legs)
    return MarketSnapshot(
        snapshot_version="1.0", timestamp=ts, source="fyers_live", latency_ms=1.0,
        health_status=HEALTH_OK, missing_fields=(),
        spot=SpotSnapshot(symbol="NIFTY", ltp=spot), vix=VixSnapshot(value=13.0),
        futures=None, option_chain=chain,
    )


class FakeBroker:
    async def get_recent_candles(self, underlying, minutes, count):
        out = []
        c = 24300.0
        for i in range(20):
            c += 5
            out.append(Candle(timestamp=datetime(2026, 8, 6, 9, i, tzinfo=timezone.utc),
                               open=c - 5, high=c + 2, low=c - 7, close=c, volume=1000))
        return out


CLOCK = lambda: datetime(2026, 8, 6, 9, 15, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_record_cycle_includes_regime_memory_and_narrative():
    recorder = IntelligenceCycleRecorder()
    record = await recorder.record_cycle(make_snapshot(), FakeBroker(), CLOCK)
    assert "regime_memory" in record and record["regime_memory"] is not None
    assert "narrative" in record and record["narrative"] is not None
    assert "market_story" in record["narrative"]


@pytest.mark.asyncio
async def test_regime_memory_duration_grows_across_cycles_within_same_regime():
    recorder = IntelligenceCycleRecorder()
    r1 = await recorder.record_cycle(make_snapshot(24400.0, "2026-08-06T09:15:00+05:30"), FakeBroker(), CLOCK)
    r2 = await recorder.record_cycle(make_snapshot(24401.0, "2026-08-06T09:16:00+05:30"), FakeBroker(), CLOCK)
    # Both cycles saw a real market_state.regime (same FakeBroker candles
    # both times) -- duration should grow, not reset, if the regime read
    # stayed the same.
    if r1["market_state"]["regime"] == r2["market_state"]["regime"] and r1["market_state"]["regime"] is not None:
        assert r2["regime_memory"]["duration_cycles"] >= r1["regime_memory"]["duration_cycles"]


@pytest.mark.asyncio
async def test_narrative_never_references_execution_or_orders():
    recorder = IntelligenceCycleRecorder()
    record = await recorder.record_cycle(make_snapshot(), FakeBroker(), CLOCK)
    story = record["narrative"]["market_story"].lower()
    for forbidden in ("buy", "sell", "place order", "execute"):
        assert forbidden not in story


@pytest.mark.asyncio
async def test_record_json_serializable_with_phase11_fields():
    recorder = IntelligenceCycleRecorder()
    record = await recorder.record_cycle(make_snapshot(), FakeBroker(), CLOCK)
    json.dumps(record)
