"""End-to-end Gate C.2 pipeline integration test — BUJJI Options OS v3.

    PositionGroupState
            |
            v
    project_whole_book_to_margin_legs()
            |
            v
    SimulatedMarginProvider
            |
            v
    MarginSnapshot + MarginExplanation
            |
            v
    Capital Utilization Intelligence
            |
            v
    explain_trade_decision()  -- combines the REAL capital_check.assess_capital
                                  decision with margin/utilization intelligence

Proves Bujji can answer both "Can I take this trade?" (the real,
authoritative ALLOW/VETO from capital_check.py) AND "Why?" (the
explanation layered on top). Zero network access anywhere in this file.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from bujji.trading_brain.risk_governor.capital_check import assess_capital
from bujji.trading_brain.risk_governor.capital_utilization import (
    UTILIZATION_BLOCKED,
    UTILIZATION_HEALTHY,
    assess_capital_utilization,
    explain_trade_decision,
)
from bujji.trading_brain.risk_governor.position_group_fold import LegFillState, LegState, PositionGroupState
from bujji.trading_brain.risk_governor.simulated_margin_provider import (
    RISK_DEFINED_RISK,
    RISK_EXCESSIVE_CAPITAL_USAGE,
    SimulatedMarginProvider,
)
from bujji.trading_brain.risk_governor.whole_book_margin_provider import (
    margin_snapshot_to_capital_check_input,
    project_whole_book_to_margin_legs,
)


def _clock(iso="2026-08-02T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _contract(symbol):
    return SimpleNamespace(contract_symbol=symbol)


def _run_full_pipeline(active_states, contracts, sides, prices, available_capital, configured_risk_capital):
    legs = project_whole_book_to_margin_legs(
        active_states, contracts, sides, prices, instrument_type="OPTIDX", product_type="MIS",
    )
    snapshot, explanation = SimulatedMarginProvider().get_portfolio_margin_with_explanation(legs, clock=_clock())
    capital_input = margin_snapshot_to_capital_check_input(
        snapshot, available_capital=available_capital, configured_risk_capital=configured_risk_capital,
    )
    capital_assessment = assess_capital(capital_input, clock=_clock())
    utilization = assess_capital_utilization(
        available_capital=available_capital,
        required_margin=snapshot.required_margin if snapshot.required_margin is not None else 0.0,
    )
    decision_explanation = explain_trade_decision(capital_assessment, explanation, utilization)
    return capital_assessment, explanation, utilization, decision_explanation


def test_can_i_take_this_trade_and_why_accepted_case():
    state = PositionGroupState(
        position_group_id="PG-ACCEPT", lifecycle_state="CONSTRUCTED",
        legs={
            "LEG-LONG": LegState(client_order_id="LEG-LONG", contract_id="C1", requested_quantity=50,
                                  submit_status="LEG_NOT_SUBMITTED", fill=LegFillState(client_order_id="LEG-LONG")),
            "LEG-SHORT": LegState(client_order_id="LEG-SHORT", contract_id="C2", requested_quantity=50,
                                   submit_status="LEG_NOT_SUBMITTED", fill=LegFillState(client_order_id="LEG-SHORT")),
        },
    )
    capital_assessment, explanation, utilization, decision = _run_full_pipeline(
        [state],
        contracts={"LEG-LONG": _contract("NSE:NIFTY26AUG24700CE"), "LEG-SHORT": _contract("NSE:NIFTY26AUG24800CE")},
        sides={"LEG-LONG": "BUY", "LEG-SHORT": "SELL"},
        prices={"LEG-LONG": 120.0, "LEG-SHORT": 80.0},
        available_capital=100_000.0, configured_risk_capital=100_000.0,
    )
    assert capital_assessment.decision == "ALLOW"
    assert decision.decision == "ALLOW"   # verbatim from the real capital_check decision
    assert explanation.risk_classification == RISK_DEFINED_RISK
    assert utilization.status == UTILIZATION_HEALTHY
    assert decision.current_margin == explanation.total_required_margin
    assert "Capital utilization" in decision.reason


def test_can_i_take_this_trade_and_why_rejected_case_matches_requested_example_shape():
    """A naked short straddle against tight configured capital -- must
    REJECT (VETO) end to end, with the explanation naming naked short
    exposure as the primary risk, matching the requested example
    format (Decision / Reason / Primary Risk / Current Margin /
    Available Capital)."""
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
    capital_assessment, explanation, utilization, decision = _run_full_pipeline(
        [state],
        contracts={"LEG-CE": _contract("NSE:NIFTY26AUG24800CE"), "LEG-PE": _contract("NSE:NIFTY26AUG24800PE")},
        sides={"LEG-CE": "SELL", "LEG-PE": "SELL"},
        prices={"LEG-CE": 90.0, "LEG-PE": 85.0},
        available_capital=100_000.0, configured_risk_capital=5_000.0,
    )
    assert capital_assessment.decision == "VETO"
    assert decision.decision == "VETO"
    assert decision.blocking_reason == "CAPITAL_EXCEEDED"
    assert utilization.status == UTILIZATION_BLOCKED
    assert decision.risk_classification == RISK_EXCESSIVE_CAPITAL_USAGE   # relabeled explanation, not a new veto
    assert decision.primary_risk == "NAKED_SHORT_EXPOSURE"
    assert decision.current_margin == explanation.total_required_margin
    assert decision.available_capital == 100_000.0
    # the example output shape: Decision / Reason / Primary Risk / Current Margin / Available Capital
    assert decision.decision and decision.reason and decision.primary_risk is not None
    assert decision.current_margin is not None and decision.available_capital is not None


def test_decision_never_disagrees_with_real_capital_check():
    """Explicit adversarial proof: no matter how the margin/utilization
    intelligence layers classify things, explain_trade_decision's own
    `decision` field is ALWAYS exactly capital_assessment.decision --
    never independently computed, never able to flip an ALLOW to a
    VETO or vice versa."""
    state = PositionGroupState(
        position_group_id="PG-SANITY", lifecycle_state="OPEN",
        legs={"LEG-1": LegState(client_order_id="LEG-1", contract_id="C1", requested_quantity=None,
                                 submit_status="LEG_ACKED",
                                 fill=LegFillState(client_order_id="LEG-1", cumulative_filled_quantity=50))},
    )
    for available, configured in [(100_000.0, 100_000.0), (100_000.0, 1.0), (0.0, 0.0)]:
        capital_assessment, explanation, utilization, decision = _run_full_pipeline(
            [state], contracts={"LEG-1": _contract("X")}, sides={"LEG-1": "SELL"}, prices={"LEG-1": 90.0},
            available_capital=available, configured_risk_capital=configured,
        )
        assert decision.decision == capital_assessment.decision
        assert decision.blocking_reason == capital_assessment.blocking_reason
