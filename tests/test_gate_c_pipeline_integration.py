"""End-to-end Gate C.1 pipeline integration test — BUJJI Options OS v3.

    PositionGroupState
            |
            v
    project_whole_book_to_margin_legs()
            |
            v
    SimulatedMarginProvider
            |
            v
    MarginSnapshot
            |
            v
    margin_snapshot_to_capital_check_input()
            |
            v
    capital_check.assess_capital()

Zero network access anywhere in this file -- SimulatedMarginProvider is
a deterministic, in-process computation only, never wired into any
real construction path. This test exists purely to prove the Gate C
scaffold's pieces compose correctly end to end, not to certify any
real margin figure.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from bujji.trading_brain.risk_governor.capital_check import assess_capital
from bujji.trading_brain.risk_governor.position_group_fold import LegFillState, LegState, PositionGroupState
from bujji.trading_brain.risk_governor.simulated_margin_provider import SimulatedMarginProvider
from bujji.trading_brain.risk_governor.whole_book_margin_provider import (
    margin_snapshot_to_capital_check_input,
    project_whole_book_to_margin_legs,
)


def _clock(iso="2026-08-02T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _contract(symbol):
    return SimpleNamespace(contract_symbol=symbol)


def _open_leg_group(pg_id, coid, filled_qty):
    return PositionGroupState(
        position_group_id=pg_id, lifecycle_state="OPEN",
        legs={coid: LegState(
            client_order_id=coid, contract_id="C1", requested_quantity=None, submit_status="LEG_ACKED",
            fill=LegFillState(client_order_id=coid, cumulative_filled_quantity=filled_qty,
                               cumulative_average_fill_price=None, reduced_quantity=0),
        )},
    )


def _run_pipeline(active_states, contracts, sides, prices, available_capital, configured_risk_capital):
    legs = project_whole_book_to_margin_legs(
        active_states, contracts, sides, prices, instrument_type="OPTIDX", product_type="MIS",
    )
    snapshot = SimulatedMarginProvider().get_portfolio_margin(legs, clock=_clock())
    capital_input = margin_snapshot_to_capital_check_input(
        snapshot, available_capital=available_capital, configured_risk_capital=configured_risk_capital,
    )
    return assess_capital(capital_input, clock=_clock())


def test_pipeline_accepts_trade_when_capital_is_sufficient():
    """A bull call spread (defined-risk, moderate margin) against ample
    configured capital should ALLOW end to end through the real chain."""
    state = PositionGroupState(
        position_group_id="PG-ACCEPT", lifecycle_state="CONSTRUCTED",
        legs={
            "LEG-LONG": LegState(client_order_id="LEG-LONG", contract_id="C1", requested_quantity=50,
                                  submit_status="LEG_NOT_SUBMITTED", fill=LegFillState(client_order_id="LEG-LONG")),
            "LEG-SHORT": LegState(client_order_id="LEG-SHORT", contract_id="C2", requested_quantity=50,
                                   submit_status="LEG_NOT_SUBMITTED", fill=LegFillState(client_order_id="LEG-SHORT")),
        },
    )
    result = _run_pipeline(
        [state],
        contracts={"LEG-LONG": _contract("NSE:NIFTY26AUG24700CE"), "LEG-SHORT": _contract("NSE:NIFTY26AUG24800CE")},
        sides={"LEG-LONG": "BUY", "LEG-SHORT": "SELL"},
        prices={"LEG-LONG": 120.0, "LEG-SHORT": 80.0},
        available_capital=100000.0, configured_risk_capital=100000.0,
    )
    # short=4000, long=6000 -> covered=4000, naked=0 -> margin = 3*4000 + 1*6000 = 18000, well under 100000
    assert result.decision == "ALLOW"
    assert result.blocking_reason is None


def test_pipeline_rejects_trade_when_simulated_margin_exceeds_available_capital():
    """A naked short straddle (no hedge, high margin) against a small
    configured risk capital should VETO end to end on CAPITAL_EXCEEDED,
    not silently ALLOW just because the snapshot was verified."""
    state = PositionGroupState(
        position_group_id="PG-REJECT", lifecycle_state="OPEN",
        legs={
            "LEG-CE": LegState(client_order_id="LEG-CE", contract_id="C1", requested_quantity=None,
                                submit_status="LEG_ACKED",
                                fill=LegFillState(client_order_id="LEG-CE", cumulative_filled_quantity=75)),
            "LEG-PE": LegState(client_order_id="LEG-PE", contract_id="C2", requested_quantity=None,
                                submit_status="LEG_ACKED",
                                fill=LegFillState(client_order_id="LEG-PE", cumulative_filled_quantity=75)),
        },
    )
    result = _run_pipeline(
        [state],
        contracts={"LEG-CE": _contract("NSE:NIFTY26AUG24800CE"), "LEG-PE": _contract("NSE:NIFTY26AUG24800PE")},
        sides={"LEG-CE": "SELL", "LEG-PE": "SELL"},
        prices={"LEG-CE": 90.0, "LEG-PE": 85.0},
        available_capital=100000.0, configured_risk_capital=5000.0,  # deliberately tiny
    )
    # short_notional = 75*90 + 75*85 = 13125, naked (no hedge) -> margin = 15*13125 = 196875 >> 5000
    assert result.decision == "VETO"
    assert result.blocking_reason == "CAPITAL_EXCEEDED"


def test_pipeline_empty_book_allows_with_zero_margin():
    """No open positions -> zero required margin, verified, and (given
    any non-negative configured capital) ALLOWs -- the trivial case."""
    result = _run_pipeline(
        [], contracts={}, sides={}, prices={},
        available_capital=100000.0, configured_risk_capital=50000.0,
    )
    assert result.decision == "ALLOW"


def test_pipeline_still_vetoes_if_margin_never_verified():
    """Sanity check that this test file isn't accidentally exercising
    a weakened capital_check -- feeding an explicitly unverified
    snapshot through the SAME connector must still VETO regardless of
    how small the margin looks."""
    from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginSnapshot

    unverified_snapshot = MarginSnapshot(
        required_margin=1.0, margin_verified=False, margin_source="WHOLE_BOOK_UNCERTIFIED",
        as_of=_clock()(), quote=None,
    )
    capital_input = margin_snapshot_to_capital_check_input(
        unverified_snapshot, available_capital=100000.0, configured_risk_capital=100000.0,
    )
    result = assess_capital(capital_input, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "MARGIN_NOT_CERTIFIED"
