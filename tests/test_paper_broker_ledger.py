"""Tests — Live Shadow Real-Time Paper Execution sprint: PaperBroker
position ledger (Part 2) -- entry_timestamp and realized PnL, never
MTM (that stays the Portfolio Valuation engine's own job)."""
from __future__ import annotations

import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract, OrderRequest

CONTRACT = OptionContract("NIFTY24250CE", "NIFTY", 24250, OptionType.CE, "2026-08-04", 75)


@pytest.mark.asyncio
async def test_open_position_carries_an_entry_timestamp():
    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 150, "CID-1", limit_price=124.15))
    positions = await broker.get_open_positions()
    assert positions[0]["entry_timestamp"] is not None


@pytest.mark.asyncio
async def test_entry_timestamp_preserved_when_adding_to_same_direction():
    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 75, "CID-1", limit_price=100.0))
    first_ts = (await broker.get_open_positions())[0]["entry_timestamp"]
    await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 75, "CID-2", limit_price=105.0))
    second_ts = (await broker.get_open_positions())[0]["entry_timestamp"]
    assert first_ts == second_ts


@pytest.mark.asyncio
async def test_no_realized_pnl_before_any_close():
    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 150, "CID-1", limit_price=100.0))
    assert broker.get_realized_pnl() == 0.0


@pytest.mark.asyncio
async def test_long_position_full_close_realizes_correct_pnl():
    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 150, "CID-1", limit_price=100.0))
    await broker.place_order(OrderRequest(CONTRACT, Side.SELL, 150, "CID-2", limit_price=110.0))
    assert broker.get_realized_pnl(CONTRACT.symbol) == pytest.approx((110.0 - 100.0) * 150)
    assert (await broker.get_open_positions()) == []  # flat, correctly removed.


@pytest.mark.asyncio
async def test_short_position_full_close_realizes_correct_pnl():
    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT, Side.SELL, 150, "CID-1", limit_price=100.0))
    await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 150, "CID-2", limit_price=90.0))
    assert broker.get_realized_pnl(CONTRACT.symbol) == pytest.approx((100.0 - 90.0) * 150)


@pytest.mark.asyncio
async def test_partial_close_realizes_only_the_closed_portion():
    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 150, "CID-1", limit_price=100.0))
    await broker.place_order(OrderRequest(CONTRACT, Side.SELL, 50, "CID-2", limit_price=110.0))
    assert broker.get_realized_pnl(CONTRACT.symbol) == pytest.approx((110.0 - 100.0) * 50)
    remaining = (await broker.get_open_positions())[0]
    assert remaining["qty"] == 100
    assert remaining["side"] == Side.BUY.value


@pytest.mark.asyncio
async def test_partial_close_preserves_original_entry_timestamp():
    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 150, "CID-1", limit_price=100.0))
    entry_ts = (await broker.get_open_positions())[0]["entry_timestamp"]
    await broker.place_order(OrderRequest(CONTRACT, Side.SELL, 50, "CID-2", limit_price=110.0))
    remaining = (await broker.get_open_positions())[0]
    assert remaining["entry_timestamp"] == entry_ts


@pytest.mark.asyncio
async def test_flip_from_long_to_short_realizes_the_closed_portion_and_starts_fresh_entry():
    broker = PaperBroker()
    await broker.connect()
    await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 100, "CID-1", limit_price=100.0))
    entry_ts = (await broker.get_open_positions())[0]["entry_timestamp"]
    # SELL 150 closes the 100 long and opens a fresh 50 short, in one fill.
    await broker.place_order(OrderRequest(CONTRACT, Side.SELL, 150, "CID-2", limit_price=110.0))
    assert broker.get_realized_pnl(CONTRACT.symbol) == pytest.approx((110.0 - 100.0) * 100)
    new_position = (await broker.get_open_positions())[0]
    assert new_position["side"] == Side.SELL.value
    assert new_position["qty"] == 50
    assert new_position["avg_price"] == 110.0


@pytest.mark.asyncio
async def test_realized_pnl_total_sums_across_symbols():
    broker = PaperBroker()
    await broker.connect()
    c2 = OptionContract("NIFTY24300CE", "NIFTY", 24300, OptionType.CE, "2026-08-04", 75)
    await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 150, "CID-1", limit_price=100.0))
    await broker.place_order(OrderRequest(CONTRACT, Side.SELL, 150, "CID-2", limit_price=110.0))
    await broker.place_order(OrderRequest(c2, Side.SELL, 150, "CID-3", limit_price=80.0))
    await broker.place_order(OrderRequest(c2, Side.BUY, 150, "CID-4", limit_price=70.0))
    expected = (110.0 - 100.0) * 150 + (80.0 - 70.0) * 150
    assert broker.get_realized_pnl() == pytest.approx(expected)


@pytest.mark.asyncio
async def test_paper_broker_never_computes_unrealized_mtm_itself():
    """Structural check (Part 2's own explicit requirement): PaperBroker
    must never compute or expose an MTM/unrealized figure -- that
    responsibility belongs entirely to the Portfolio Valuation engine."""
    broker = PaperBroker()
    attrs = [a for a in vars(broker) if "unrealized" in a.lower() or "mtm" in a.lower()]
    assert attrs == []
