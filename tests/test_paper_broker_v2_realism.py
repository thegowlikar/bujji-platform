"""PaperBroker v2 -- the realism that makes 30 sessions of learning data
trustworthy rather than merely plentiful.

Three gaps, each of which quietly biased the evidence rather than
breaking anything loudly:
  1. Fills were size-blind -- a structure unfillable at real size scored
     identically to a genuinely liquid one.
  2. Orders were never margin-checked -- the book could hold positions no
     real account could carry.
  3. MFE/MAE were None on every record ever written -- how a trade
     BEHAVED was unrecoverable, only how it ended.
"""
from __future__ import annotations

import pytest

from bujji.broker.factory import _production_paper_broker
from bujji.broker.paper import PaperBroker
from bujji.broker.simulation.fill_simulator import _depth_impact_multiplier
from bujji.core.enums import OptionType, OrderStatus, Side
from bujji.core.models import OptionContract, OrderRequest
from bujji.outcome_attribution.engine import compute_mfe_mae

CE = OptionContract("NIFTY25200CE", "NIFTY", 25200, OptionType.CE, "2026-08-27", 75)


class TestDepthAwareFills:
    def test_order_inside_available_depth_is_unpenalised(self):
        assert _depth_impact_multiplier(50, 100) == 1.0
        assert _depth_impact_multiplier(100, 100) == 1.0

    def test_impact_scales_with_how_far_past_depth_the_order_reaches(self):
        assert _depth_impact_multiplier(200, 100) == 2.0
        assert _depth_impact_multiplier(500, 100) == 5.0

    def test_unknown_depth_is_never_penalised_on_a_guess(self):
        assert _depth_impact_multiplier(10_000, None) == 1.0
        assert _depth_impact_multiplier(10_000, 0) == 1.0

    @pytest.mark.asyncio
    async def test_a_large_order_on_a_thin_wing_fills_worse_than_a_small_one(self):
        """The whole point: size must change the answer."""
        from bujji.broker.simulation.slippage import SlippageConfig, SlippageMode
        cfg = SlippageConfig(mode=SlippageMode.PERCENTAGE, percentage=0.01)

        small = PaperBroker(slippage_config=cfg)
        await small.connect()
        small.set_depth("NIFTY25200CE", 75)
        small_fill = await small.place_order(OrderRequest(CE, Side.BUY, 75, "S", reference_price=100.0))

        large = PaperBroker(slippage_config=cfg)
        await large.connect()
        large.set_depth("NIFTY25200CE", 75)
        large_fill = await large.place_order(OrderRequest(CE, Side.BUY, 300, "L", reference_price=100.0))

        assert large_fill.average_price > small_fill.average_price


class TestMarginRejection:
    @pytest.mark.asyncio
    async def test_an_order_beyond_free_margin_is_rejected(self):
        broker = PaperBroker(available_margin=1_00_000.0, margin_per_lot=1_00_000.0, enforce_margin=True)
        await broker.connect()
        # 300 units / lot 75 = 4 lots -> 4,00,000 needed, only 1,00,000 free.
        result = await broker.place_order(OrderRequest(CE, Side.SELL, 300, "TOO-BIG", reference_price=100.0))
        assert result.status == OrderStatus.REJECTED
        assert "INSUFFICIENT_MARGIN" in result.message
        assert await broker.get_open_positions() == []

    @pytest.mark.asyncio
    async def test_an_affordable_order_still_fills(self):
        broker = PaperBroker(available_margin=10_00_000.0, margin_per_lot=1_00_000.0, enforce_margin=True)
        await broker.connect()
        result = await broker.place_order(OrderRequest(CE, Side.SELL, 75, "OK", reference_price=100.0))
        assert result.status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_an_exit_is_never_blocked_by_margin(self):
        """Refusing an exit because the account is fully committed is how
        a real book gets trapped. Reducing orders release margin and must
        always be allowed."""
        broker = PaperBroker(available_margin=1_00_000.0, margin_per_lot=1_00_000.0, enforce_margin=True)
        await broker.connect()
        await broker.place_order(OrderRequest(CE, Side.SELL, 75, "OPEN", reference_price=100.0))
        assert broker.used_margin() == pytest.approx(1_00_000.0)  # fully committed.
        exit_result = await broker.place_order(OrderRequest(CE, Side.BUY, 75, "EXIT", reference_price=90.0))
        assert exit_result.status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_enforcement_is_off_by_default(self):
        broker = PaperBroker(available_margin=1.0, margin_per_lot=1_00_000.0)
        await broker.connect()
        result = await broker.place_order(OrderRequest(CE, Side.SELL, 300, "X", reference_price=100.0))
        assert result.status == OrderStatus.FILLED  # unchanged legacy behaviour.

    def test_production_broker_enforces_margin(self):
        assert _production_paper_broker()._enforce_margin is True


class TestMfeMae:
    def test_excursions_come_from_real_history(self):
        mfe, mae = compute_mfe_mae([100.0, -250.0, 400.0, 50.0])
        assert mfe == 400.0   # best it ever looked
        assert mae == -250.0  # worst it ever looked

    def test_unpriced_cycles_are_dropped_not_treated_as_flat(self):
        mfe, mae = compute_mfe_mae([None, -100.0, None, 300.0])
        assert (mfe, mae) == (300.0, -100.0)

    def test_no_history_stays_honestly_none(self):
        assert compute_mfe_mae([]) == (None, None)
        assert compute_mfe_mae([None, None]) == (None, None)

    def test_a_winner_that_was_underwater_is_distinguishable_from_one_that_never_was(self):
        """Exactly what MFE/MAE exist to record: both trades end +500,
        but only one of them was ever in trouble."""
        smooth_mfe, smooth_mae = compute_mfe_mae([100.0, 300.0, 500.0])
        rocky_mfe, rocky_mae = compute_mfe_mae([-800.0, -200.0, 500.0])
        assert smooth_mae == 100.0
        assert rocky_mae == -800.0
        assert smooth_mfe == rocky_mfe == 500.0
