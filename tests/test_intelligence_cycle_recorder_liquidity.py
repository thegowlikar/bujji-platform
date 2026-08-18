"""Tests -- Intelligence Cycle Recorder Liquidity Intelligence Bridge,
Phase 9. FakeBroker only, no live calls. Mirrors
test_intelligence_cycle_recorder_extended.py's fixtures exactly."""
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


def make_snapshot(spot=24400.0, ts="2026-08-06T09:15:00+05:30", ce_bid=99.0, ce_ask=101.0, pe_bid=88.0, pe_ask=90.0,
                   option_chain=True):
    if not option_chain:
        chain = None
    else:
        legs = (
            OptionLeg(symbol="CE", strike=24450.0, option_type="CE", ltp=None, bid=ce_bid, ask=ce_ask,
                      spread=ce_ask - ce_bid if ce_bid is not None and ce_ask is not None else None,
                      volume=None, open_interest=12000.0, iv=None, delta=None, gamma=None, theta=None, vega=None),
            OptionLeg(symbol="PE", strike=24450.0, option_type="PE", ltp=None, bid=pe_bid, ask=pe_ask,
                      spread=pe_ask - pe_bid if pe_bid is not None and pe_ask is not None else None,
                      volume=None, open_interest=15000.0, iv=None, delta=None, gamma=None, theta=None, vega=None),
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
async def test_record_cycle_includes_liquidity_key():
    recorder = IntelligenceCycleRecorder()
    record = await recorder.record_cycle(make_snapshot(), FakeBroker(), CLOCK)
    assert "liquidity" in record
    assert record["liquidity"] is not None
    assert "tightness" in record["liquidity"]


@pytest.mark.asyncio
async def test_liquidity_reading_reflects_real_atm_bid_ask_tight_quote():
    """Tight quote (small spread relative to mid) -> real TIGHT/NORMAL
    tightness, never fabricated, never defaulted to UNKNOWN when real
    data is present."""
    recorder = IntelligenceCycleRecorder()
    record = await recorder.record_cycle(
        make_snapshot(ce_bid=100.0, ce_ask=100.3, pe_bid=100.0, pe_ask=100.3), FakeBroker(), CLOCK,
    )
    assert record["liquidity"]["tightness"] == "TIGHT"
    assert record["liquidity"]["ce_bid"] == 100.0


@pytest.mark.asyncio
async def test_liquidity_reading_honestly_wide_on_wide_quote():
    recorder = IntelligenceCycleRecorder()
    record = await recorder.record_cycle(
        make_snapshot(ce_bid=100.0, ce_ask=104.0, pe_bid=100.0, pe_ask=104.0), FakeBroker(), CLOCK,
    )
    assert record["liquidity"]["tightness"] == "WIDE"


@pytest.mark.asyncio
async def test_liquidity_reading_honestly_unknown_when_option_chain_missing():
    """No option chain on the snapshot at all -- never fabricates a
    tight/normal/wide reading, honestly reports UNKNOWN."""
    recorder = IntelligenceCycleRecorder()
    record = await recorder.record_cycle(make_snapshot(option_chain=False), FakeBroker(), CLOCK)
    assert record["liquidity"]["tightness"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_liquidity_flows_into_synthetic_iron_condor_iron_fly_suitability():
    """End-to-end: a real tight liquidity reading this cycle should let
    liquidity-gated families (SYNTHETIC/IRON_CONDOR/IRON_FLY) stop
    reporting LIQUIDITY as missing evidence -- the actual point of this
    bridge, verified through the full recorder, not just the engine
    directly."""
    recorder = IntelligenceCycleRecorder()
    await recorder.record_cycle(
        make_snapshot(24400.0, "2026-08-06T09:15:00+05:30", ce_bid=100.0, ce_ask=100.3, pe_bid=100.0, pe_ask=100.3),
        FakeBroker(), CLOCK,
    )
    record = await recorder.record_cycle(
        make_snapshot(24460.0, "2026-08-06T09:16:00+05:30", ce_bid=100.0, ce_ask=100.3, pe_bid=100.0, pe_ask=100.3),
        FakeBroker(), CLOCK,
    )
    assert record["liquidity"]["tightness"] == "TIGHT"
    by_family = {row["strategy_family"]: row for row in record["strategy_suitability"]}
    for family in ("SYNTHETIC", "IRON_CONDOR", "IRON_FLY"):
        assert "LIQUIDITY" not in by_family[family]["required_missing_evidence"], (
            f"{family} should no longer report liquidity missing with a real tight reading"
        )


@pytest.mark.asyncio
async def test_record_cycle_fully_json_serializable_with_liquidity():
    recorder = IntelligenceCycleRecorder()
    await recorder.record_cycle(make_snapshot(24400.0, "2026-08-06T09:15:00+05:30"), FakeBroker(), CLOCK)
    record = await recorder.record_cycle(make_snapshot(24460.0, "2026-08-06T09:16:00+05:30"), FakeBroker(), CLOCK)
    json.dumps(record)  # no `default=str` needed -- to_log() is already plain JSON-safe types.


@pytest.mark.asyncio
async def test_liquidity_never_influences_families_that_do_not_require_it():
    """A WIDE liquidity reading must not affect LONG_DIRECTIONAL (which
    does not require DOMAIN_LIQUIDITY at all) -- evidence is scoped
    strictly to the families that declare they consume it."""
    recorder = IntelligenceCycleRecorder()
    await recorder.record_cycle(
        make_snapshot(24400.0, "2026-08-06T09:15:00+05:30", ce_bid=100.0, ce_ask=104.0, pe_bid=100.0, pe_ask=104.0),
        FakeBroker(), CLOCK,
    )
    record = await recorder.record_cycle(
        make_snapshot(24460.0, "2026-08-06T09:16:00+05:30", ce_bid=100.0, ce_ask=104.0, pe_bid=100.0, pe_ask=104.0),
        FakeBroker(), CLOCK,
    )
    by_family = {row["strategy_family"]: row for row in record["strategy_suitability"]}
    assert "LIQUIDITY" not in by_family["LONG_DIRECTIONAL"]["required_missing_evidence"]
