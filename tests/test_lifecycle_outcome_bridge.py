"""The learning loop, closed.

Proves the exact chain the Phase 1-3 audit found broken: a real
PaperBroker fill -> canonical PositionLifecycle -> real exit ->
OutcomeAttribution -> OutcomeMemoryRecord. Before the bridge, the live
path stopped at an in-memory enum flip and produced no memory record at
all, so 30 paper sessions would have taught Bujji nothing.

Uses the REAL PaperBroker, the REAL msi_trade_construction leg shape,
and the REAL canonical reducer -- no mocks.
"""
from __future__ import annotations

import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract, OrderRequest
from bujji.msi_trade_construction.models import StrikeLeg
from bujji.position_lifecycle.models import STATUS_CLOSED, STATUS_OPEN
from bujji.production_runtime.lifecycle_outcome_bridge import (
    attribute_and_remember,
    close_position,
    open_position,
)

SESSION = "SESSION-2026-08-17"
ENTRY_TS = "2026-08-17T09:20:00+05:30"
EXIT_TS = "2026-08-17T15:15:00+05:30"
LOT = 75

CE = OptionContract("NIFTY25200CE", "NIFTY", 25200, OptionType.CE, "2026-08-27", LOT)
PE = OptionContract("NIFTY24800PE", "NIFTY", 24800, OptionType.PE, "2026-08-27", LOT)


def _leg(role, option_type, strike, side, premium):
    return StrikeLeg(
        role=role, option_type=option_type, strike=strike, expiry="2026-08-27",
        delta=0.2, premium=premium, open_interest=100000.0, side=side, ratio=1,
        reasoning=("test fixture",),
    )


class TestDuplicateRoles:
    """Real structures reuse a role across legs -- msi_trade_construction
    builds an IRON_CONDOR as SHORT/SHORT/WING_UPPER/WING_LOWER. An
    earlier draft of this bridge keyed fill prices by `leg.role`, which
    silently collapsed the two SHORT legs into one dict entry: one short
    leg's real fill price was dropped and the other's recorded twice,
    corrupting the cost basis of a position before it was ever exited.
    Fills are positional precisely so this cannot happen."""

    def test_iron_condor_duplicate_short_roles_keep_distinct_fills(self):
        legs = (
            _leg("SHORT", "CE", 25200.0, "SELL", 100.0),
            _leg("SHORT", "PE", 24800.0, "SELL", 90.0),
            _leg("WING_UPPER", "CE", 25400.0, "BUY", 40.0),
            _leg("WING_LOWER", "PE", 24600.0, "BUY", 35.0),
        )
        states, position_id, outcome = open_position(
            {}, SESSION, position_group_id="PG-IC", strategy_family="IRON_CONDOR",
            legs=legs, fill_prices=[101.5, 91.5, 39.0, 34.0], entry_timestamp=ENTRY_TS,
            underlying_symbol="NIFTY", underlying_price=25000.0, lot_size=LOT,
        )
        assert outcome == "ACCEPTED"
        premiums = [leg.entry_premium for leg in states[position_id].legs]
        # Every leg keeps its OWN fill -- no collision between the two SHORTs.
        assert premiums == [101.5, 91.5, 39.0, 34.0]

    def test_a_leg_that_never_filled_does_not_misalign_the_rest(self):
        legs = (
            _leg("SHORT", "CE", 25200.0, "SELL", 100.0),
            _leg("SHORT", "PE", 24800.0, "SELL", 90.0),
        )
        # Only the first leg filled.
        states, position_id, _ = open_position(
            {}, SESSION, position_group_id="PG-SHORTFILL", strategy_family="SHORT_STRANGLE",
            legs=legs, fill_prices=[101.5], entry_timestamp=ENTRY_TS,
            underlying_symbol="NIFTY", underlying_price=25000.0, lot_size=LOT,
        )
        premiums = [leg.entry_premium for leg in states[position_id].legs]
        assert premiums[0] == 101.5
        # Falls back to that leg's own observed premium, NOT the first leg's fill.
        assert premiums[1] == 90.0


class TestFullLoop:
    @pytest.mark.asyncio
    async def test_fill_to_outcome_memory_end_to_end(self):
        broker = PaperBroker()
        await broker.connect()
        ce_result = await broker.place_order(OrderRequest(CE, Side.SELL, LOT, "E-CE", reference_price=100.0))
        pe_result = await broker.place_order(OrderRequest(PE, Side.SELL, LOT, "E-PE", reference_price=90.0))

        legs = (
            _leg("SHORT", "CE", 25200.0, "SELL", 100.0),
            _leg("SHORT", "PE", 24800.0, "SELL", 90.0),
        )
        states, position_id, outcome = open_position(
            {}, SESSION, position_group_id="PG-1", strategy_family="SHORT_STRANGLE",
            legs=legs, fill_prices=[ce_result.average_price, pe_result.average_price], entry_timestamp=ENTRY_TS,
            underlying_symbol="NIFTY", underlying_price=25000.0, lot_size=LOT,
            market_regime="RANGE_PERSISTENCE", direction="NEUTRAL",
            thesis="premium_decay", selection_confidence="HIGH",
        )
        assert outcome == "ACCEPTED"
        assert position_id is not None
        lifecycle = states[position_id]
        assert lifecycle.status == STATUS_OPEN
        assert len(lifecycle.legs) == 2
        # The REAL fill price became the cost basis, not the quote.
        assert lifecycle.legs[0].entry_premium == ce_result.average_price

        # Exit both legs cheaper -- a premium-selling win.
        exit_prices = {
            lifecycle.legs[0].leg_id: {"exit_price": 40.0},
            lifecycle.legs[1].leg_id: {"exit_price": 30.0},
        }
        states, close_outcome = close_position(
            states, SESSION, position_id, exit_timestamp=EXIT_TS,
            exit_reason="MANDATORY_EXIT", exit_prices=exit_prices,
        )
        assert close_outcome == "ACCEPTED"
        assert states[position_id].status == STATUS_CLOSED

        structured = states[position_id].structured_exit
        assert structured is not None
        # Sold 100+90, bought back 40+30 -> 120 points x 75 = 9000.
        assert structured["gross_realized_pnl"] == pytest.approx(9000.0)

        states, record, mem_outcome = attribute_and_remember(
            states, SESSION, position_id, recorded_at=EXIT_TS,
        )
        assert mem_outcome == "ACCEPTED"
        assert record is not None, "the learning loop must produce a real memory record"
        assert record.strategy_family == "SHORT_STRANGLE"
        assert record.entry_regime == "RANGE_PERSISTENCE"
        assert record.realized_pnl == pytest.approx(9000.0)
        assert record.outcome_direction == "PROFIT"

    @pytest.mark.asyncio
    async def test_a_losing_trade_is_recorded_just_as_faithfully(self):
        broker = PaperBroker()
        await broker.connect()
        result = await broker.place_order(OrderRequest(CE, Side.SELL, LOT, "E-CE", reference_price=100.0))

        legs = (_leg("SHORT_CALL", "CE", 25200.0, "SELL", 100.0),)
        states, position_id, _ = open_position(
            {}, SESSION, position_group_id="PG-LOSS", strategy_family="SHORT_STRANGLE",
            legs=legs, fill_prices=[result.average_price],
            entry_timestamp=ENTRY_TS, underlying_symbol="NIFTY", underlying_price=25000.0,
            lot_size=LOT, market_regime="TREND_UP",
        )
        lifecycle = states[position_id]
        # Sold at 100, forced to buy back at 180 -- a real loss.
        states, _ = close_position(
            states, SESSION, position_id, exit_timestamp=EXIT_TS, exit_reason="MAX_LOSS",
            exit_prices={lifecycle.legs[0].leg_id: {"exit_price": 180.0}},
        )
        states, record, outcome = attribute_and_remember(
            states, SESSION, position_id, recorded_at=EXIT_TS,
        )
        assert outcome == "ACCEPTED"
        assert record.realized_pnl == pytest.approx(-6000.0)  # -80 x 75
        assert record.outcome_direction == "LOSS"


class TestHonestDegradation:
    @pytest.mark.asyncio
    async def test_a_leg_with_no_exit_price_is_unknown_not_assumed(self):
        legs = (
            _leg("SHORT_CALL", "CE", 25200.0, "SELL", 100.0),
            _leg("SHORT_PUT", "PE", 24800.0, "SELL", 90.0),
        )
        states, position_id, _ = open_position(
            {}, SESSION, position_group_id="PG-PARTIAL", strategy_family="SHORT_STRANGLE",
            legs=legs, fill_prices=[100.0, 90.0],
            entry_timestamp=ENTRY_TS, underlying_symbol="NIFTY", underlying_price=25000.0,
            lot_size=LOT,
        )
        lifecycle = states[position_id]
        # Only ONE leg has a real exit price.
        states, _ = close_position(
            states, SESSION, position_id, exit_timestamp=EXIT_TS, exit_reason="MANDATORY_EXIT",
            exit_prices={lifecycle.legs[0].leg_id: {"exit_price": 40.0}},
        )
        structured = states[position_id].structured_exit
        assert structured["pnl_status"] != "PNL_KNOWN"

    def test_closing_an_unknown_position_is_reported_not_crashed(self):
        states, outcome = close_position(
            {}, SESSION, "NO-SUCH-ID", exit_timestamp=EXIT_TS,
            exit_reason="MANDATORY_EXIT", exit_prices={},
        )
        assert outcome == "NO_SUCH_POSITION"

    def test_open_position_is_never_remembered(self):
        legs = (_leg("SHORT_CALL", "CE", 25200.0, "SELL", 100.0),)
        states, position_id, _ = open_position(
            {}, SESSION, position_group_id="PG-OPEN", strategy_family="SHORT_STRANGLE",
            legs=legs, fill_prices=[100.0], entry_timestamp=ENTRY_TS,
            underlying_symbol="NIFTY", underlying_price=25000.0, lot_size=LOT,
        )
        states, record, outcome = attribute_and_remember(
            states, SESSION, position_id, recorded_at=EXIT_TS,
        )
        assert record is None
        assert outcome == "NOT_CLOSED"


class TestNoExecutionCapability:
    def test_bridge_cannot_place_or_cancel_an_order(self):
        """Structural: this module records what happened, it never
        decides or executes. Checked against bound names, not a source
        substring (this docstring itself mentions place_order)."""
        from bujji.production_runtime import lifecycle_outcome_bridge as mod
        names = dir(mod)
        for forbidden in ("PaperBroker", "Broker", "place_order", "cancel_order", "select_strategy"):
            assert forbidden not in names
