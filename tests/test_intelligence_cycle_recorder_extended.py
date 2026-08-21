"""Tests -- Intelligence Cycle Recorder extension (Strategy Suitability,
Selection, Trade Intent), 2026-08-06. FakeBroker only, no live calls."""
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


def make_snapshot(spot=24400.0, ts="2026-08-06T09:15:00+05:30", ce_oi=12000.0):
    legs = (
        OptionLeg(symbol="CE", strike=24450.0, option_type="CE", ltp=None, bid=99.0, ask=101.0,
                  spread=2.0, volume=None, open_interest=ce_oi, iv=None, delta=None, gamma=None,
                  theta=None, vega=None),
        OptionLeg(symbol="PE", strike=24450.0, option_type="PE", ltp=None, bid=88.0, ask=90.0,
                  spread=2.0, volume=None, open_interest=15000.0, iv=None, delta=None, gamma=None,
                  theta=None, vega=None),
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


@pytest.mark.asyncio
async def test_record_cycle_includes_new_keys():
    recorder = IntelligenceCycleRecorder()
    broker = FakeBroker()
    clock = lambda: datetime(2026, 8, 6, 9, 15, tzinfo=timezone.utc)
    record = await recorder.record_cycle(make_snapshot(), broker, clock)
    for key in ("strategy_suitability", "strategy_selection", "trade_intent"):
        assert key in record


@pytest.mark.asyncio
async def test_first_cycle_produces_a_real_but_empty_selection():
    recorder = IntelligenceCycleRecorder()
    broker = FakeBroker()
    clock = lambda: datetime(2026, 8, 6, 9, 15, tzinfo=timezone.utc)
    record = await recorder.record_cycle(make_snapshot(), broker, clock)
    # market_state_builder already produces real psi/mssi from the very
    # first OBSERVATION_CREATED event (established since Phase 3B) --
    # so select_strategy() DOES run and returns a real
    # StrategySelectionAssessment object. Its selected_strategy_family
    # is honestly None (no suitable+eligible family exists this early),
    # never a fabricated selection.
    assert record["strategy_selection"] is not None
    assert record["strategy_selection"]["selected_strategy_family"] is None


@pytest.mark.asyncio
async def test_second_cycle_produces_real_suitability_assessments():
    recorder = IntelligenceCycleRecorder()
    broker = FakeBroker()
    clock = lambda: datetime(2026, 8, 6, 9, 15, tzinfo=timezone.utc)
    await recorder.record_cycle(make_snapshot(24400.0, "2026-08-06T09:15:00+05:30"), broker, clock)
    record = await recorder.record_cycle(make_snapshot(24460.0, "2026-08-06T09:16:00+05:30"), broker, clock)
    assert len(record["strategy_suitability"]) == 13  # one per taxonomy.ALL_STRATEGY_FAMILIES
    for row in record["strategy_suitability"]:
        assert "strategy_family" in row
        assert "suitability" in row


@pytest.mark.asyncio
async def test_record_cycle_fully_json_serializable_with_new_fields():
    recorder = IntelligenceCycleRecorder()
    broker = FakeBroker()
    clock = lambda: datetime(2026, 8, 6, 9, 15, tzinfo=timezone.utc)
    await recorder.record_cycle(make_snapshot(24400.0, "2026-08-06T09:15:00+05:30"), broker, clock)
    record = await recorder.record_cycle(make_snapshot(24460.0, "2026-08-06T09:16:00+05:30"), broker, clock)
    json.dumps(record, default=str)


@pytest.mark.asyncio
async def test_trade_intent_never_fabricated_when_no_eligibility():
    recorder = IntelligenceCycleRecorder()
    broker = FakeBroker()
    clock = lambda: datetime(2026, 8, 6, 9, 15, tzinfo=timezone.utc)
    record = await recorder.record_cycle(make_snapshot(), broker, clock)
    if record["strategy_eligibility"] is None:
        assert record["trade_intent"] is None
