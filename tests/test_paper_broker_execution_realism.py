"""Execution realism: spread and slippage now actually cost something.

Both mechanisms were built and tested, then left switched off at every
real construction site -- so live/shadow P&L was computed as if crossing
the spread and market impact were free. For a 4-leg premium-selling
structure that understates cost on every leg of every trade.
"""
from __future__ import annotations

import pytest

from bujji.broker.factory import _production_paper_broker
from bujji.broker.paper import PaperBroker
from bujji.broker.simulation.fill_simulator import _reference_price_for_side
from bujji.broker.simulation.market_snapshot import MarketSnapshot
from bujji.broker.simulation.slippage import SlippageConfig, SlippageMode
from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract, OrderRequest

CE = OptionContract("NIFTY25200CE", "NIFTY", 25200, OptionType.CE, "2026-08-27", 75)


def _snap(**kw):
    base = dict(symbol="NIFTY25200CE", last_price=100.0)
    base.update(kw)
    return MarketSnapshot(**base)


class TestSideOfBook:
    def test_buy_lifts_the_ask(self):
        assert _reference_price_for_side(_snap(bid=99.0, ask=101.0), "BUY") == 101.0

    def test_sell_hits_the_bid(self):
        assert _reference_price_for_side(_snap(bid=99.0, ask=101.0), "SELL") == 99.0

    def test_missing_quote_falls_back_to_last_price(self):
        assert _reference_price_for_side(_snap(), "BUY") == 100.0
        assert _reference_price_for_side(_snap(), "SELL") == 100.0

    def test_a_half_quoted_book_is_not_reconstructed_from_the_other_side(self):
        """Only the ask is known. A BUY uses it; a SELL must NOT invent a
        bid from it -- that would fabricate a spread never observed."""
        s = _snap(ask=101.0)
        assert _reference_price_for_side(s, "BUY") == 101.0
        assert _reference_price_for_side(s, "SELL") == 100.0

    def test_a_crossed_book_is_reported_as_observed_not_corrected(self):
        """bid > ask is a real data-quality signal. Silently "fixing" it
        to a mid would hide the problem behind a plausible number."""
        s = _snap(bid=102.0, ask=98.0)
        assert _reference_price_for_side(s, "BUY") == 98.0
        assert _reference_price_for_side(s, "SELL") == 102.0


class TestSpreadCostsRealMoney:
    @pytest.mark.asyncio
    async def test_round_trip_across_the_spread_loses_money(self):
        """THE point of the change: sell at the bid, buy back at the ask,
        with the mid unmoved -- that must be a LOSS, not breakeven."""
        broker = PaperBroker()
        await broker.connect()
        broker.set_quote("NIFTY25200CE", bid=99.0, ask=101.0)

        await broker.place_order(OrderRequest(CE, Side.SELL, 75, "S", reference_price=100.0))
        await broker.place_order(OrderRequest(CE, Side.BUY, 75, "B", reference_price=100.0))

        # Sold at 99, bought back at 101 -> -2 x 75 = -150.
        assert broker.get_realized_pnl("NIFTY25200CE") == pytest.approx(-150.0)

    @pytest.mark.asyncio
    async def test_without_a_quote_the_same_round_trip_is_free(self):
        """Confirms the change is opt-in: no quote supplied, no spread
        cost, byte-identical to the old behaviour."""
        broker = PaperBroker()
        await broker.connect()
        await broker.place_order(OrderRequest(CE, Side.SELL, 75, "S", reference_price=100.0))
        await broker.place_order(OrderRequest(CE, Side.BUY, 75, "B", reference_price=100.0))
        assert broker.get_realized_pnl("NIFTY25200CE") == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_spread_and_slippage_both_apply_and_do_not_substitute(self):
        """They model different costs -- crossing the book vs adverse
        impact -- so slippage lands ON TOP of the correct side."""
        broker = PaperBroker(slippage_config=SlippageConfig(mode=SlippageMode.PERCENTAGE, percentage=0.01))
        await broker.connect()
        broker.set_quote("NIFTY25200CE", bid=99.0, ask=101.0)
        result = await broker.place_order(OrderRequest(CE, Side.BUY, 75, "B", reference_price=100.0))
        # Ask 101 + 1% adverse = 102.01, not 100 + 1%.
        assert result.average_price == pytest.approx(102.01)


class TestProductionProfileIsOn:
    def test_production_broker_has_non_zero_slippage(self):
        broker = _production_paper_broker()
        assert broker._slippage_config.mode != SlippageMode.ZERO

    def test_a_bare_paper_broker_is_still_frictionless(self):
        """The realism is applied at the production construction site, NOT
        as PaperBroker's default -- so no existing test's assertions move."""
        assert PaperBroker()._slippage_config.mode == SlippageMode.ZERO

    @pytest.mark.asyncio
    async def test_production_broker_fill_is_adverse_to_the_order(self):
        broker = _production_paper_broker()
        await broker.connect()
        buy = await broker.place_order(OrderRequest(CE, Side.BUY, 75, "B", reference_price=100.0))
        sell = await broker.place_order(OrderRequest(CE, Side.SELL, 75, "S", reference_price=100.0))
        assert buy.average_price > 100.0   # paid up
        assert sell.average_price < 100.0  # received less
