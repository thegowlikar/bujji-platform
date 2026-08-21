"""Tests -- Option Chain Adapter, Shadow Campaign v2 Phase 1.

Uses a small synthetic instrument-master CSV fixture (not the real,
possibly-stale cache) so strike resolution is deterministic and
independent of live market state. FakeBroker only -- no live FYERS
calls anywhere in this file.
"""
from __future__ import annotations

import csv
import time

import pytest

from bujji.market_perception.models import OptionChainConfig
from bujji.market_perception.option_chain_adapter import (
    build_option_chain_snapshot,
    resolve_chain_contracts,
)

N_COLS = 17


def _row(lot_size, expiry_epoch, symbol, underlying, strike, option_type):
    row = [""] * N_COLS
    row[3] = str(lot_size)
    row[8] = str(expiry_epoch)
    row[9] = symbol
    row[13] = underlying
    row[15] = str(strike)
    row[16] = option_type
    return row


@pytest.fixture
def cache_file(tmp_path):
    future_expiry = int(time.time()) + 3 * 86400
    expired = int(time.time()) - 3 * 86400
    path = tmp_path / "instrument_master.csv"
    rows = []
    for strike in (24300, 24400, 24500, 24600, 24700, 24800, 24900, 26800):
        for opt_type in ("CE", "PE"):
            symbol = f"NSE:NIFTY26AUG{strike}{opt_type}"
            rows.append(_row(75, future_expiry, symbol, "NIFTY", strike, opt_type))
    # An expired row that must be excluded.
    rows.append(_row(75, expired, "NSE:NIFTY26JUL24600CE", "NIFTY", 24600, "CE"))
    # A different underlying that must be excluded.
    rows.append(_row(25, future_expiry, "NSE:BANKNIFTY26AUG50000CE", "BANKNIFTY", 50000, "CE"))
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    return str(path)


class FakeBroker:
    def __init__(self, quote_response=None, chain_response=None):
        self._quote_response = quote_response
        self._chain_response = chain_response
        self.quote_calls = []
        self.chain_calls = 0

    async def get_quote(self, contract):
        self.quote_calls.append(contract.symbol)
        return self._quote_response

    async def get_option_chain(self, underlying, spot, strike_count=5):
        self.chain_calls += 1
        return self._chain_response


def test_resolve_chain_contracts_filters_by_range_and_expiry(cache_file):
    cfg = OptionChainConfig(strike_range=200, strike_step=100)
    expiry, contracts = resolve_chain_contracts("NIFTY", 24600.0, cfg, cache_file)
    strikes = sorted({c.strike for c in contracts})
    assert strikes == [24400, 24500, 24600, 24700, 24800]
    assert all(c.underlying == "NIFTY" for c in contracts)
    assert expiry  # a real ISO date string, not empty


def test_resolve_chain_contracts_excludes_expired_and_other_underlying(cache_file):
    cfg = OptionChainConfig(strike_range=2000, strike_step=100)
    _, contracts = resolve_chain_contracts("NIFTY", 24600.0, cfg, cache_file)
    symbols = {c.symbol for c in contracts}
    assert "NSE:NIFTY26JUL24600CE" not in symbols
    assert not any("BANKNIFTY" in s for s in symbols)


def test_resolve_chain_contracts_no_data_returns_empty(tmp_path):
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("")
    cfg = OptionChainConfig()
    expiry, contracts = resolve_chain_contracts("NIFTY", 24600.0, cfg, str(empty_csv))
    assert expiry == ""
    assert contracts == []


@pytest.mark.asyncio
async def test_build_option_chain_snapshot_ce_pe_mapping(cache_file):
    cfg = OptionChainConfig(strike_range=200, strike_step=100)
    broker = FakeBroker(
        quote_response={"bid": 99.0, "ask": 101.0, "spread": 2.0},
        chain_response=[(24600.0, 12000.0, 15000.0)],
    )
    snapshot = await build_option_chain_snapshot(broker, "NIFTY", 24600.0, cfg, cache_file)

    assert snapshot is not None
    assert snapshot.atm_strike == 24600.0
    ce = snapshot.leg(24600.0, "CE")
    pe = snapshot.leg(24600.0, "PE")
    assert ce.open_interest == 12000.0
    assert pe.open_interest == 15000.0
    assert ce.bid == 99.0 and ce.ask == 101.0
    # Fields honestly unavailable this phase.
    assert ce.volume is None and ce.iv is None and ce.delta is None


@pytest.mark.asyncio
async def test_build_option_chain_snapshot_quote_failure_leaves_bid_ask_none(cache_file):
    cfg = OptionChainConfig(strike_range=100, strike_step=100)

    class FailingQuoteBroker(FakeBroker):
        async def get_quote(self, contract):
            raise RuntimeError("simulated broker failure")

    broker = FailingQuoteBroker(chain_response=[])
    snapshot = await build_option_chain_snapshot(broker, "NIFTY", 24600.0, cfg, cache_file)
    assert snapshot is not None
    for leg in snapshot.legs:
        assert leg.bid is None and leg.ask is None


@pytest.mark.asyncio
async def test_build_option_chain_snapshot_no_contracts_returns_none(tmp_path):
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("")
    cfg = OptionChainConfig()
    broker = FakeBroker()
    snapshot = await build_option_chain_snapshot(broker, "NIFTY", 24600.0, cfg, str(empty_csv))
    assert snapshot is None
