"""End-to-end Gate C.3 pipeline integration test — BUJJI Options OS v3.

    Whole Book State
            |
            v
    project_whole_book_to_margin_legs()
            |
            v
    SimulatedMarginProvider
            |
            v
    FyersMarginProvider (mocked, READ-ONLY -- no real broker connection)
            |
            v
    MarginComparisonEngine

Zero network access anywhere in this file. The "FyersMarginProvider"
stage uses a fake, in-memory broker object -- no real Fyers/broker
credentials or connections are ever constructed here, matching this
whole phase's explicit safety boundary.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bujji.core.enums import OptionType
from bujji.core.models import OptionContract
from bujji.trading_brain.risk_governor.broker_margin_reality_adapter import FyersMarginProvider
from bujji.trading_brain.risk_governor.margin_comparison_engine import (
    COMPARISON_PASS,
    COMPARISON_UNAVAILABLE,
    compare_margin,
)
from bujji.trading_brain.risk_governor.position_group_fold import LegFillState, LegState, PositionGroupState
from bujji.trading_brain.risk_governor.simulated_margin_provider import SimulatedMarginProvider
from bujji.trading_brain.risk_governor.whole_book_margin_provider import project_whole_book_to_margin_legs


def _clock(iso="2026-08-02T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _contract_stub(symbol):
    return SimpleNamespace(contract_symbol=symbol)


class _MockReadOnlyBroker:
    """Fake, in-memory broker -- no place_order/cancel_order exist on
    this object at all. Stands in for a real, credentialed FyersBroker
    without ever constructing one."""

    def __init__(self, funds, order_margin):
        self._funds = funds
        self._order_margin = order_margin

    async def get_funds(self):
        return self._funds

    async def get_order_margin(self, ce_contract, pe_contract):
        return self._order_margin


@pytest.mark.asyncio
async def test_full_pipeline_short_straddle_pass():
    """Matches the requested worked example: a NIFTY short straddle
    whose simulated and broker-reported margins are close (PASS)."""
    state = PositionGroupState(
        position_group_id="PG-1", lifecycle_state="OPEN",
        legs={
            "LEG-CE": LegState(client_order_id="LEG-CE", contract_id="C1", requested_quantity=None,
                                submit_status="LEG_ACKED",
                                fill=LegFillState(client_order_id="LEG-CE", cumulative_filled_quantity=75)),
            "LEG-PE": LegState(client_order_id="LEG-PE", contract_id="C2", requested_quantity=None,
                                submit_status="LEG_ACKED",
                                fill=LegFillState(client_order_id="LEG-PE", cumulative_filled_quantity=75)),
        },
    )
    legs = project_whole_book_to_margin_legs(
        [state],
        contracts_by_client_order_id={"LEG-CE": _contract_stub("NSE:NIFTY26AUG24800CE"),
                                       "LEG-PE": _contract_stub("NSE:NIFTY26AUG24800PE")},
        sides_by_client_order_id={"LEG-CE": "SELL", "LEG-PE": "SELL"},
        reference_prices_by_client_order_id={"LEG-CE": 300.0, "LEG-PE": 280.0},
        instrument_type="OPTIDX", product_type="MIS",
    )
    simulated_snapshot, _ = SimulatedMarginProvider().get_portfolio_margin_with_explanation(legs, clock=_clock())

    ce_contract = OptionContract(symbol="NSE:NIFTY26AUG24800CE", underlying="NIFTY", strike=24800,
                                  option_type=OptionType.CE, expiry="2026-08-27", lot_size=75)
    pe_contract = OptionContract(symbol="NSE:NIFTY26AUG24800PE", underlying="NIFTY", strike=24800,
                                  option_type=OptionType.PE, expiry="2026-08-27", lot_size=75)

    # broker figure deliberately close to (but not exactly) the simulated one
    close_broker_margin = simulated_snapshot.required_margin * 1.012
    mock_broker = _MockReadOnlyBroker(
        funds={"available_margin": 1_000_000.0, "used_margin": 50_000.0},
        order_margin={"margin_per_lot": close_broker_margin, "verified": False, "source": "fyers_span_margin"},
    )
    broker_provider = FyersMarginProvider(mock_broker)
    broker_snapshot = await broker_provider.get_broker_margin_snapshot(ce_contract, pe_contract, clock=_clock())

    report = compare_margin(simulated_snapshot, broker_snapshot, clock=_clock())
    assert report.status == COMPARISON_PASS
    assert report.simulated_margin == simulated_snapshot.required_margin
    assert report.broker_margin == pytest.approx(close_broker_margin)
    assert report.deviation_fraction < 0.05


@pytest.mark.asyncio
async def test_full_pipeline_broker_unavailable_never_falls_back_to_zero():
    """A broker outage anywhere in the pipeline must surface as
    UNAVAILABLE, never as a silently-safe PASS or a zeroed margin."""
    state = PositionGroupState(
        position_group_id="PG-2", lifecycle_state="OPEN",
        legs={"LEG-CE": LegState(client_order_id="LEG-CE", contract_id="C1", requested_quantity=None,
                                  submit_status="LEG_ACKED",
                                  fill=LegFillState(client_order_id="LEG-CE", cumulative_filled_quantity=75))},
    )
    legs = project_whole_book_to_margin_legs(
        [state], contracts_by_client_order_id={"LEG-CE": _contract_stub("NSE:NIFTY26AUG24800CE")},
        sides_by_client_order_id={"LEG-CE": "SELL"}, reference_prices_by_client_order_id={"LEG-CE": 90.0},
        instrument_type="OPTIDX", product_type="MIS",
    )
    simulated_snapshot, _ = SimulatedMarginProvider().get_portfolio_margin_with_explanation(legs, clock=_clock())

    ce_contract = OptionContract(symbol="NSE:NIFTY26AUG24800CE", underlying="NIFTY", strike=24800,
                                  option_type=OptionType.CE, expiry="2026-08-27", lot_size=75)
    pe_contract = OptionContract(symbol="NSE:NIFTY26AUG24800PE", underlying="NIFTY", strike=24700,
                                  option_type=OptionType.PE, expiry="2026-08-27", lot_size=75)

    down_broker = _MockReadOnlyBroker(funds=None, order_margin=None)  # broker outage
    broker_provider = FyersMarginProvider(down_broker)
    broker_snapshot = await broker_provider.get_broker_margin_snapshot(ce_contract, pe_contract, clock=_clock())

    report = compare_margin(simulated_snapshot, broker_snapshot, clock=_clock())
    assert report.status == COMPARISON_UNAVAILABLE
    assert report.broker_margin is None
    assert simulated_snapshot.required_margin is not None  # the SIMULATION remains usable regardless
