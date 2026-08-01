"""Tests — Numeric Risk Governor Gate C.2.5 (margin stress & scenario
engine). Zero network access anywhere in this file."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bujji.trading_brain.risk_governor.capital_utilization import UTILIZATION_HEALTHY, UTILIZATION_WARNING
from bujji.trading_brain.risk_governor.margin_scenario_engine import (
    MarginScenarioEngine,
    MarginScenarioRequest,
)
from bujji.trading_brain.risk_governor.simulated_margin_provider import (
    RISK_HIGH_NAKED_EXPOSURE,
    SimulatedMarginProvider,
)
from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginLegRequest


def _clock(iso="2026-08-02T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _leg(symbol, qty, side, price):
    return MarginLegRequest(symbol=symbol, qty=qty, side=side, instrument_type="OPTIDX",
                             product_type="MIS", limit_price=price)


def _short_straddle(prefix, qty=50, ce_price=90.0, pe_price=85.0):
    return (
        _leg(f"{prefix}-CE", qty, -1, ce_price),
        _leg(f"{prefix}-PE", qty, -1, pe_price),
    )


# --------------------------------------------------------------------- #
# Scenario 1 -- adding a trade increases margin
# --------------------------------------------------------------------- #

def test_adding_trade_increases_margin():
    engine = MarginScenarioEngine()
    current = _short_straddle("S1")
    request = MarginScenarioRequest(current_legs=current, added_legs=_short_straddle("S2"))
    result = engine.run(request, clock=_clock())
    assert result.stressed_snapshot.required_margin > result.baseline_snapshot.required_margin
    assert result.margin_change == pytest.approx(
        result.stressed_snapshot.required_margin - result.baseline_snapshot.required_margin
    )
    assert result.margin_change > 0


# --------------------------------------------------------------------- #
# Scenario 2 -- removing a trade decreases margin
# --------------------------------------------------------------------- #

def test_removing_trade_decreases_margin():
    engine = MarginScenarioEngine()
    current = _short_straddle("S1") + _short_straddle("S2")
    request = MarginScenarioRequest(current_legs=current, removed_symbols=("S2-CE", "S2-PE"))
    result = engine.run(request, clock=_clock())
    assert result.stressed_snapshot.required_margin < result.baseline_snapshot.required_margin
    assert result.margin_change < 0


# --------------------------------------------------------------------- #
# Scenario 3 -- hedging improves capital usage
# --------------------------------------------------------------------- #

def test_hedge_improves_capital_utilization():
    engine = MarginScenarioEngine()
    naked_short = (_leg("CE-1", 50, -1, 100.0),)
    hedge_leg = (_leg("CE-2", 50, 1, 60.0),)  # a long call at a further strike, offsetting notional
    request = MarginScenarioRequest(
        current_legs=naked_short, added_legs=hedge_leg, available_capital=100_000.0,
    )
    result = engine.run(request, clock=_clock())
    assert result.stressed_snapshot.required_margin < result.baseline_snapshot.required_margin
    assert result.utilization_after.usage_fraction < result.utilization_before.usage_fraction


# --------------------------------------------------------------------- #
# Scenario 4 -- quantity doubling scales margin deterministically
# --------------------------------------------------------------------- #

def test_quantity_doubling_scales_margin_proportionally():
    engine = MarginScenarioEngine()
    current = _short_straddle("S1")
    request = MarginScenarioRequest(current_legs=current, quantity_multiplier=2.0)
    result = engine.run(request, clock=_clock())
    assert result.stressed_snapshot.required_margin == pytest.approx(result.baseline_snapshot.required_margin * 2.0)


def test_quantity_scaling_is_deterministic_repeatable():
    engine = MarginScenarioEngine()
    request = MarginScenarioRequest(current_legs=_short_straddle("S1"), quantity_multiplier=1.5)
    r1 = engine.run(request, clock=_clock())
    r2 = engine.run(request, clock=_clock())
    assert r1.stressed_snapshot.required_margin == r2.stressed_snapshot.required_margin


# --------------------------------------------------------------------- #
# Scenario 5 -- multi-position whole-book scenario
# --------------------------------------------------------------------- #

def test_multi_position_scenario_includes_all_positions():
    engine = MarginScenarioEngine()
    current = _short_straddle("S1") + _short_straddle("S2") + (_leg("HEDGE", 50, 1, 40.0),)
    request = MarginScenarioRequest(current_legs=current, added_legs=(_leg("S3-CE", 50, -1, 95.0),))
    result = engine.run(request, clock=_clock())
    assert len(result.stressed_explanation.contributing_legs) == 6  # 5 current + 1 added
    symbols = {c.symbol for c in result.stressed_explanation.contributing_legs}
    assert symbols == {"S1-CE", "S1-PE", "S2-CE", "S2-PE", "HEDGE", "S3-CE"}


# --------------------------------------------------------------------- #
# Price shift (Scenario E)
# --------------------------------------------------------------------- #

def test_price_shift_percent_increases_margin_deterministically():
    engine = MarginScenarioEngine()
    request = MarginScenarioRequest(current_legs=(_leg("CE-1", 50, -1, 100.0),), price_shift_percent=10.0)
    result = engine.run(request, clock=_clock())
    # new price = 110.0, notional = 5500, naked short (no hedge) -> margin = 15 * 5500
    assert result.stressed_snapshot.required_margin == pytest.approx(15.0 * 50 * 110.0)


def test_negative_price_shift_decreases_margin():
    engine = MarginScenarioEngine()
    request = MarginScenarioRequest(current_legs=(_leg("CE-1", 50, -1, 100.0),), price_shift_percent=-10.0)
    result = engine.run(request, clock=_clock())
    assert result.stressed_snapshot.required_margin < result.baseline_snapshot.required_margin


# --------------------------------------------------------------------- #
# Safety test 6 -- cannot mutate original portfolio
# --------------------------------------------------------------------- #

def test_scenario_engine_never_mutates_input_legs():
    engine = MarginScenarioEngine()
    original_leg = _leg("CE-1", 50, -1, 100.0)
    current = (original_leg,)
    request = MarginScenarioRequest(
        current_legs=current, quantity_multiplier=2.0, price_shift_percent=10.0,
        added_legs=(_leg("CE-2", 25, 1, 40.0),),
    )
    engine.run(request, clock=_clock())
    # the ORIGINAL leg object (and the tuple holding it) must be byte-identical after the run
    assert original_leg.qty == 50
    assert original_leg.limit_price == 100.0
    assert current[0] is original_leg
    assert request.current_legs == (original_leg,)


# --------------------------------------------------------------------- #
# Safety test 7 -- scenario result matches direct recalculation
# --------------------------------------------------------------------- #

def test_scenario_result_matches_direct_provider_recalculation():
    engine = MarginScenarioEngine()
    current = _short_straddle("S1")
    added = (_leg("S2-CE", 50, -1, 95.0),)
    request = MarginScenarioRequest(current_legs=current, added_legs=added)
    result = engine.run(request, clock=_clock())

    # independently recompute the "new portfolio" directly via SimulatedMarginProvider,
    # bypassing the scenario engine entirely
    direct_legs = list(current) + list(added)
    direct_snapshot, direct_explanation = SimulatedMarginProvider().get_portfolio_margin_with_explanation(
        direct_legs, clock=_clock(),
    )
    assert result.stressed_snapshot.required_margin == direct_snapshot.required_margin
    assert result.stressed_explanation.total_required_margin == direct_explanation.total_required_margin


# --------------------------------------------------------------------- #
# Safety test 8 -- decision cannot disagree with capital_check
# --------------------------------------------------------------------- #

def test_scenario_decision_never_disagrees_with_real_capital_check():
    from bujji.trading_brain.risk_governor.capital_check import assess_capital
    from bujji.trading_brain.risk_governor.whole_book_margin_provider import margin_snapshot_to_capital_check_input

    engine = MarginScenarioEngine()
    request = MarginScenarioRequest(
        current_legs=_short_straddle("S1"), added_legs=_short_straddle("S2"),
        available_capital=100_000.0, configured_risk_capital=10_000.0,  # deliberately tiny -> forced VETO
    )
    result = engine.run(request, clock=_clock())

    # independently recompute the real capital_check decision from the scenario's own stressed snapshot
    capital_input = margin_snapshot_to_capital_check_input(
        result.stressed_snapshot, available_capital=100_000.0, configured_risk_capital=10_000.0,
    )
    independent_assessment = assess_capital(capital_input, clock=_clock())
    assert result.capital_assessment.decision == independent_assessment.decision
    assert result.capital_assessment.decision == "VETO"
    assert result.decision.decision == result.capital_assessment.decision


def test_scenario_decision_allows_when_capital_is_ample():
    engine = MarginScenarioEngine()
    request = MarginScenarioRequest(
        current_legs=(), added_legs=(_leg("CE-1", 10, -1, 50.0),),
        available_capital=1_000_000.0, configured_risk_capital=1_000_000.0,
    )
    result = engine.run(request, clock=_clock())
    assert result.capital_assessment.decision == "ALLOW"
    assert result.decision.decision == "ALLOW"


# --------------------------------------------------------------------- #
# Safety test 9 -- invalid scenario data fails closed
# --------------------------------------------------------------------- #

def test_negative_quantity_multiplier_result_fails_closed_via_underlying_validation():
    engine = MarginScenarioEngine()
    # a multiplier that drives qty to <= 0 must trigger the provider's own
    # existing non-positive-qty validation, not silently produce a
    # nonsensical negative-quantity margin request.
    request = MarginScenarioRequest(current_legs=(_leg("CE-1", 50, -1, 100.0),), quantity_multiplier=0.0)
    result = engine.run(request, clock=_clock())
    assert result.stressed_snapshot.margin_verified is False
    assert result.stressed_snapshot.required_margin is None


def test_malformed_added_leg_fails_closed():
    engine = MarginScenarioEngine()
    malformed_leg = MarginLegRequest(symbol="BAD", qty=50, side=-1, instrument_type="OPTIDX",
                                      product_type="MIS", limit_price=None)  # missing price
    request = MarginScenarioRequest(current_legs=_short_straddle("S1"), added_legs=(malformed_leg,))
    result = engine.run(request, clock=_clock())
    assert result.stressed_snapshot.margin_verified is False
    assert result.stressed_explanation.risk_classification == "INVALID_STATE"
    # the BASELINE (before adding the malformed leg) must still be valid -- one bad leg in the
    # scenario must not retroactively corrupt the baseline computation
    assert result.baseline_snapshot.margin_verified is True


def test_missing_available_capital_produces_no_decision_not_a_fake_one():
    engine = MarginScenarioEngine()
    request = MarginScenarioRequest(current_legs=_short_straddle("S1"))  # no capital context supplied
    result = engine.run(request, clock=_clock())
    assert result.utilization_before is None
    assert result.utilization_after is None
    assert result.capital_assessment is None
    assert result.decision is None
