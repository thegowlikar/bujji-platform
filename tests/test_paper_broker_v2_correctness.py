"""PaperBroker v2 -- correctness fixes found by the Phase 1-3 audit.

Three real defects, each proven here against the REAL PaperBroker (no
mocks, no stubs):

  1. `avg_price` stored the LATEST fill price instead of the position's
     true cost basis, corrupting realized PnL on any partial reduction.
  2. `cancel_order` returned CANCELLED unconditionally, so a cancel
     against an already-filled order reported a success that never
     happened.
  3. `used_margin` was hardcoded 0.0, so free margin never moved, an
     exhausted account was unrepresentable, and exit released nothing.
"""
from __future__ import annotations

import pytest

from bujji.broker.paper import PaperBroker, _resulting_avg_price
from bujji.core.enums import OptionType, OrderStatus, Side
from bujji.core.models import OptionContract, OrderRequest

CE = OptionContract("NIFTY25000CE", "NIFTY", 25000, OptionType.CE, "2026-08-27", 75)


# --------------------------------------------------------------------- #
# 1. Cost basis
# --------------------------------------------------------------------- #
class TestCostBasis:
    def test_fresh_open_uses_the_fill_price(self):
        assert _resulting_avg_price(prev_net=0, prev_avg=None, delta=-75, net=-75, price=120.0) == 120.0

    def test_same_direction_add_is_quantity_weighted(self):
        # Short 75 @ 100, add another short 75 @ 140 -> basis 120.
        assert _resulting_avg_price(prev_net=-75, prev_avg=100.0, delta=-75, net=-150, price=140.0) == 120.0

    def test_uneven_add_is_weighted_by_quantity_not_averaged_naively(self):
        # Short 100 @ 100, add short 300 @ 200 -> (100*100 + 300*200)/400 = 175.
        result = _resulting_avg_price(prev_net=-100, prev_avg=100.0, delta=-300, net=-400, price=200.0)
        assert result == 175.0

    def test_partial_reduction_leaves_the_basis_untouched(self):
        # THE BUG: short 100 @ 120, buy back 50 @ 100. The remaining 50
        # still cost 120 -- it does not suddenly cost 100.
        assert _resulting_avg_price(prev_net=-100, prev_avg=120.0, delta=50, net=-50, price=100.0) == 120.0

    def test_direction_flip_starts_a_new_basis(self):
        assert _resulting_avg_price(prev_net=-50, prev_avg=120.0, delta=100, net=50, price=90.0) == 90.0

    @pytest.mark.asyncio
    async def test_realized_pnl_is_correct_across_a_staged_exit(self):
        """End-to-end consequence of the bug, through the real broker.

        Short 150 @ 120, then close in two halves at 100 and 90. True
        profit = (120-100)*75 + (120-90)*75 = 1500 + 2250 = 3750. The old
        code re-based the remainder to 100 after the first exit and
        reported only (100-90)*75 = 750 on the second, understating the
        trade by 1500.
        """
        broker = PaperBroker()
        await broker.connect()
        await broker.place_order(OrderRequest(CE, Side.SELL, 150, "OPEN", reference_price=120.0))
        await broker.place_order(OrderRequest(CE, Side.BUY, 75, "EXIT-1", reference_price=100.0))
        # Basis of the surviving half is still the original 120.
        remaining = (await broker.get_open_positions())[0]
        assert remaining["avg_price"] == 120.0
        await broker.place_order(OrderRequest(CE, Side.BUY, 75, "EXIT-2", reference_price=90.0))
        assert broker.get_realized_pnl("NIFTY25000CE") == pytest.approx(3750.0)


# --------------------------------------------------------------------- #
# 2. Cancellation honesty
# --------------------------------------------------------------------- #
class TestCancellation:
    @pytest.mark.asyncio
    async def test_filled_order_cannot_be_cancelled(self):
        broker = PaperBroker()
        await broker.connect()
        await broker.place_order(OrderRequest(CE, Side.SELL, 75, "CID", reference_price=120.0))
        result = await broker.cancel_order("CID")
        assert result.status == OrderStatus.FILLED
        assert result.message == "not_cancellable:filled"

    @pytest.mark.asyncio
    async def test_unknown_order_is_not_a_silent_success(self):
        broker = PaperBroker()
        await broker.connect()
        result = await broker.cancel_order("NEVER-PLACED")
        assert result.status == OrderStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_open_remainder_cancels_and_persists(self):
        broker = PaperBroker(partial_fill_qty=25)
        await broker.connect()
        await broker.place_order(OrderRequest(CE, Side.SELL, 75, "CID", reference_price=120.0))
        result = await broker.cancel_order("CID")
        assert result.status == OrderStatus.CANCELLED
        assert result.filled_quantity == 25  # real fills survive a cancel.
        assert (await broker.get_order("CID")).status == OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancelling_twice_is_not_reported_as_two_successes(self):
        broker = PaperBroker(partial_fill_qty=25)
        await broker.connect()
        await broker.place_order(OrderRequest(CE, Side.SELL, 75, "CID", reference_price=120.0))
        assert (await broker.cancel_order("CID")).status == OrderStatus.CANCELLED
        second = await broker.cancel_order("CID")
        assert second.message == "not_cancellable:cancelled"


# --------------------------------------------------------------------- #
# 3. Margin ledger
# --------------------------------------------------------------------- #
class TestMarginLedger:
    @pytest.mark.asyncio
    async def test_no_open_position_blocks_nothing(self):
        broker = PaperBroker()
        await broker.connect()
        funds = await broker.get_funds()
        assert funds["used_margin"] == 0.0
        assert funds["available_margin"] == 1_00_00_000.0

    @pytest.mark.asyncio
    async def test_short_blocks_margin_per_lot_and_reduces_free_margin(self):
        broker = PaperBroker(available_margin=10_00_000.0, margin_per_lot=1_00_000.0)
        await broker.connect()
        # 150 units / lot_size 75 = 2 lots -> 2 x 1,00,000 = 2,00,000.
        await broker.place_order(OrderRequest(CE, Side.SELL, 150, "OPEN", reference_price=120.0))
        funds = await broker.get_funds()
        assert funds["used_margin"] == pytest.approx(2_00_000.0)
        assert funds["available_margin"] == pytest.approx(8_00_000.0)

    @pytest.mark.asyncio
    async def test_exit_releases_the_margin(self):
        broker = PaperBroker(available_margin=10_00_000.0, margin_per_lot=1_00_000.0)
        await broker.connect()
        await broker.place_order(OrderRequest(CE, Side.SELL, 150, "OPEN", reference_price=120.0))
        await broker.place_order(OrderRequest(CE, Side.BUY, 150, "CLOSE", reference_price=110.0))
        funds = await broker.get_funds()
        assert funds["used_margin"] == 0.0
        assert funds["available_margin"] == pytest.approx(10_00_000.0)

    @pytest.mark.asyncio
    async def test_partial_exit_releases_proportionally(self):
        broker = PaperBroker(available_margin=10_00_000.0, margin_per_lot=1_00_000.0)
        await broker.connect()
        await broker.place_order(OrderRequest(CE, Side.SELL, 150, "OPEN", reference_price=120.0))
        await broker.place_order(OrderRequest(CE, Side.BUY, 75, "HALF", reference_price=110.0))
        funds = await broker.get_funds()
        assert funds["used_margin"] == pytest.approx(1_00_000.0)  # 1 lot still short.

    @pytest.mark.asyncio
    async def test_long_blocks_premium_not_lot_margin(self):
        broker = PaperBroker(available_margin=10_00_000.0, margin_per_lot=1_00_000.0)
        await broker.connect()
        # Buying costs premium: 75 x 120 = 9,000 -- not a lot margin.
        await broker.place_order(OrderRequest(CE, Side.BUY, 75, "LONG", reference_price=120.0))
        funds = await broker.get_funds()
        assert funds["used_margin"] == pytest.approx(9_000.0)

    @pytest.mark.asyncio
    async def test_free_margin_never_goes_negative(self):
        broker = PaperBroker(available_margin=50_000.0, margin_per_lot=1_00_000.0)
        await broker.connect()
        await broker.place_order(OrderRequest(CE, Side.SELL, 150, "OPEN", reference_price=120.0))
        funds = await broker.get_funds()
        assert funds["available_margin"] == 0.0
        assert funds["used_margin"] > funds["peak_margin"]  # honestly over-blocked, not clamped away.

    @pytest.mark.asyncio
    async def test_used_margin_is_summed_from_the_real_per_symbol_ledger(self):
        pe = OptionContract("NIFTY25000PE", "NIFTY", 25000, OptionType.PE, "2026-08-27", 75)
        broker = PaperBroker(available_margin=10_00_000.0, margin_per_lot=1_00_000.0)
        await broker.connect()
        await broker.place_order(OrderRequest(CE, Side.SELL, 75, "CE-LEG", reference_price=120.0))
        await broker.place_order(OrderRequest(pe, Side.SELL, 75, "PE-LEG", reference_price=110.0))
        # A short straddle blocks each leg independently -- disclosed as
        # deliberately conservative (no spread netting credit).
        assert broker.used_margin() == pytest.approx(2_00_000.0)
