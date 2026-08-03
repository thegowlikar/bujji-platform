"""Tests -- Execution Reality Layer, Phase-0 (quote capture only).

Covers: quote retrieval/normalization correctness, all five
data_quality failure states, freshness re-validation, timestamp
ordering, storage round-trip, and -- critically -- confirms no
existing trading module imports anything from execution_reality/.
Uses a fake broker (no live Fyers dependency in the test suite itself;
real-quote cross-checking is a manual Phase-0 validation step, not an
automated test, per the Phase-0 plan's own Part 5).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract
from bujji.execution_reality.models import (
    DATA_QUALITY_INVALID, DATA_QUALITY_LIVE_QUOTE, DATA_QUALITY_STALE, DATA_QUALITY_UNAVAILABLE,
    RAW_STATUS_CALL_FAILED, RAW_STATUS_NONE_RETURNED, RAW_STATUS_RESPONDED, LegQuote, QuoteObservationRecord,
)
from bujji.execution_reality.market_quote_adapter import MarketQuoteAdapter
from bujji.execution_reality.quote_observation_store import QuoteObservationStore, build_quote_observation_record

CONTRACT = OptionContract("NIFTY25000CE", "NIFTY", 25000, OptionType.CE, "2026-08-04", 75)
FIXED_NOW = datetime(2026, 8, 4, 9, 30, 0, tzinfo=timezone.utc)


def clock():
    return FIXED_NOW


class FakeBroker:
    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises
        self.calls = 0

    async def get_quote(self, contract):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._response


# --------------------------------------------------------------------- #
# Normalization -- valid bid + ask
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_valid_bid_ask_produces_live_quote():
    broker = FakeBroker(response={"bid": 98.0, "ask": 100.0, "spread": 2.0})
    adapter = MarketQuoteAdapter(broker, clock)
    quote, raw_status = await adapter.fetch(CONTRACT, Side.SELL)

    assert raw_status == RAW_STATUS_RESPONDED
    assert quote.data_quality == DATA_QUALITY_LIVE_QUOTE
    assert quote.bid == 98.0
    assert quote.ask == 100.0
    assert quote.mid == pytest.approx(99.0)
    assert quote.absolute_spread == pytest.approx(2.0)
    assert quote.spread_percentage == pytest.approx(2.0 / 99.0)
    assert quote.timestamp == FIXED_NOW.isoformat()
    assert quote.side == Side.SELL
    assert broker.calls == 1


# --------------------------------------------------------------------- #
# Failure case 1 -- broker returns None
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_none_response_is_unavailable():
    broker = FakeBroker(response=None)
    adapter = MarketQuoteAdapter(broker, clock)
    quote, raw_status = await adapter.fetch(CONTRACT, Side.SELL)

    assert raw_status == RAW_STATUS_NONE_RETURNED
    assert quote.data_quality == DATA_QUALITY_UNAVAILABLE
    assert quote.bid is None and quote.ask is None
    assert quote.mid is None and quote.absolute_spread is None and quote.spread_percentage is None


# --------------------------------------------------------------------- #
# Failure case 2/3 -- missing bid or ask
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_missing_bid_is_unavailable():
    broker = FakeBroker(response={"ask": 100.0})
    adapter = MarketQuoteAdapter(broker, clock)
    quote, raw_status = await adapter.fetch(CONTRACT, Side.SELL)
    assert raw_status == RAW_STATUS_RESPONDED
    assert quote.data_quality == DATA_QUALITY_UNAVAILABLE


@pytest.mark.asyncio
async def test_missing_ask_is_unavailable():
    broker = FakeBroker(response={"bid": 98.0})
    adapter = MarketQuoteAdapter(broker, clock)
    quote, raw_status = await adapter.fetch(CONTRACT, Side.SELL)
    assert raw_status == RAW_STATUS_RESPONDED
    assert quote.data_quality == DATA_QUALITY_UNAVAILABLE


# --------------------------------------------------------------------- #
# Failure case 4 -- invalid prices
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
@pytest.mark.parametrize("bid,ask", [(0.0, 100.0), (98.0, 0.0), (-5.0, 100.0), (100.0, 98.0)])
async def test_invalid_prices_produce_invalid_and_no_derived_values(bid, ask):
    broker = FakeBroker(response={"bid": bid, "ask": ask})
    adapter = MarketQuoteAdapter(broker, clock)
    quote, raw_status = await adapter.fetch(CONTRACT, Side.SELL)

    assert raw_status == RAW_STATUS_RESPONDED
    assert quote.data_quality == DATA_QUALITY_INVALID
    assert quote.bid is None and quote.ask is None
    assert quote.mid is None and quote.absolute_spread is None and quote.spread_percentage is None


# --------------------------------------------------------------------- #
# Broker call itself raises
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_broker_call_exception_is_call_failed():
    broker = FakeBroker(raises=RuntimeError("connection reset"))
    adapter = MarketQuoteAdapter(broker, clock)
    quote, raw_status = await adapter.fetch(CONTRACT, Side.SELL)

    assert raw_status == RAW_STATUS_CALL_FAILED
    assert quote.data_quality == DATA_QUALITY_UNAVAILABLE


# --------------------------------------------------------------------- #
# Failure case 5 -- stale timestamp
# --------------------------------------------------------------------- #

def test_revalidate_freshness_downgrades_old_live_quote_to_stale():
    old_timestamp = (FIXED_NOW - timedelta(seconds=30)).isoformat()
    live_quote = LegQuote(
        symbol="NIFTY25000CE", strike=25000.0, option_type="CE", side=Side.SELL,
        bid=98.0, ask=100.0, mid=99.0, absolute_spread=2.0, spread_percentage=2.0 / 99.0,
        data_quality=DATA_QUALITY_LIVE_QUOTE, timestamp=old_timestamp,
    )
    revalidated = MarketQuoteAdapter.revalidate_freshness(live_quote, FIXED_NOW, max_age_seconds=5.0)
    assert revalidated.data_quality == DATA_QUALITY_STALE
    # bid/ask/spread are preserved -- STALE means "aging," not "invalid"
    assert revalidated.bid == 98.0 and revalidated.mid == 99.0


def test_revalidate_freshness_leaves_fresh_quote_unchanged():
    fresh_timestamp = (FIXED_NOW - timedelta(seconds=1)).isoformat()
    live_quote = LegQuote(
        symbol="NIFTY25000CE", strike=25000.0, option_type="CE", side=Side.SELL,
        bid=98.0, ask=100.0, mid=99.0, absolute_spread=2.0, spread_percentage=2.0 / 99.0,
        data_quality=DATA_QUALITY_LIVE_QUOTE, timestamp=fresh_timestamp,
    )
    revalidated = MarketQuoteAdapter.revalidate_freshness(live_quote, FIXED_NOW, max_age_seconds=5.0)
    assert revalidated.data_quality == DATA_QUALITY_LIVE_QUOTE
    assert revalidated is live_quote  # untouched, same object -- no needless copy on the happy path


def test_revalidate_freshness_never_touches_unavailable_quote():
    unavailable = LegQuote(
        symbol="NIFTY25000CE", strike=25000.0, option_type="CE", side=Side.SELL,
        bid=None, ask=None, mid=None, absolute_spread=None, spread_percentage=None,
        data_quality=DATA_QUALITY_UNAVAILABLE, timestamp=(FIXED_NOW - timedelta(hours=1)).isoformat(),
    )
    revalidated = MarketQuoteAdapter.revalidate_freshness(unavailable, FIXED_NOW, max_age_seconds=5.0)
    assert revalidated.data_quality == DATA_QUALITY_UNAVAILABLE  # never "upgraded" or altered


# --------------------------------------------------------------------- #
# QuoteObservationRecord construction + storage
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_build_and_store_observation_record(tmp_path):
    broker = FakeBroker(response={"bid": 98.0, "ask": 100.0, "spread": 2.0})
    adapter = MarketQuoteAdapter(broker, clock)
    quote, raw_status = await adapter.fetch(CONTRACT, Side.SELL)

    recorded_at = (FIXED_NOW + timedelta(milliseconds=5)).isoformat()  # artifact time strictly after observation time
    record = build_quote_observation_record(
        CONTRACT, Side.SELL, exchange="NSE", broker_source="FakeBroker", leg_quote=quote,
        raw_source_status=raw_status, recorded_at=recorded_at,
    )
    assert record.symbol == "NIFTY25000CE"
    assert record.exchange == "NSE"
    assert record.expiry == "2026-08-04"
    assert record.normalized_result is quote
    # timestamp ordering: artifact storage time strictly after broker observation time
    assert record.timestamp > record.normalized_result.timestamp

    store = QuoteObservationStore(tmp_path / "quote_observations.jsonl")
    store.append(record)
    assert store.write_errors == []

    rows = store.read_all()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "NIFTY25000CE"
    assert rows[0]["normalized_result"]["data_quality"] == DATA_QUALITY_LIVE_QUOTE
    assert rows[0]["normalized_result"]["bid"] == 98.0
    assert rows[0]["side"] == "SELL"


def test_store_write_failure_is_recorded_not_raised(tmp_path):
    # Point the store at a path whose parent cannot be created (a file, not a directory).
    blocking_file = tmp_path / "not_a_directory"
    blocking_file.write_text("x")
    store = QuoteObservationStore(blocking_file / "sub" / "quote_observations.jsonl")
    assert store.write_errors  # mkdir failure recorded, not raised


def test_quote_observation_record_rejects_invalid_raw_status():
    quote = LegQuote(
        symbol="X", strike=1.0, option_type="CE", side=Side.SELL, bid=None, ask=None, mid=None,
        absolute_spread=None, spread_percentage=None, data_quality=DATA_QUALITY_UNAVAILABLE, timestamp=None,
    )
    with pytest.raises(ValueError):
        QuoteObservationRecord(
            symbol="X", exchange="NSE", expiry="2026-08-04", strike=1.0, option_type="CE", side=Side.SELL,
            timestamp="now", broker_source="Fake", raw_source_status="NOT_A_REAL_STATUS", normalized_result=quote,
        )


def test_leg_quote_rejects_invalid_data_quality():
    with pytest.raises(ValueError):
        LegQuote(
            symbol="X", strike=1.0, option_type="CE", side=Side.SELL, bid=None, ask=None, mid=None,
            absolute_spread=None, spread_percentage=None, data_quality="NOT_A_REAL_QUALITY", timestamp=None,
        )


# --------------------------------------------------------------------- #
# Final safety check -- no existing trading module imports execution_reality
# --------------------------------------------------------------------- #

def test_no_existing_trading_module_imports_execution_reality():
    import subprocess
    result = subprocess.run(
        ["grep", "-rl", "execution_reality",
         "bujji/production_runtime/", "bujji/trading_brain/", "bujji/msi_trade_construction/",
         "bujji/shadow_observatory/"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"unexpected references found: {result.stdout}"
