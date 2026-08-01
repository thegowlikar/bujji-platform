"""Tests — Numeric Risk Governor Gate C.2 (margin explanation & risk
classification). Zero network access anywhere in this file."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bujji.trading_brain.risk_governor.simulated_margin_provider import (
    HEDGED_MARGIN_RATE,
    LONG_MARGIN_RATE,
    RISK_DEFINED_RISK,
    RISK_HIGH_NAKED_EXPOSURE,
    RISK_INVALID_STATE,
    RISK_LOW_RISK,
    SHORT_MARGIN_RATE,
    SimulatedMarginProvider,
    classify_book_risk,
)
from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginLegRequest


def _clock(iso="2026-08-02T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _leg(symbol, qty, side, price):
    return MarginLegRequest(symbol=symbol, qty=qty, side=side, instrument_type="OPTIDX",
                             product_type="MIS", limit_price=price)


# --------------------------------------------------------------------- #
# Case 1 -- single naked short
# --------------------------------------------------------------------- #

def test_single_naked_short_identifies_exposure_and_contributor():
    provider = SimulatedMarginProvider()
    legs = [_leg("NSE:NIFTY26AUG24800CE", 50, -1, 100.0)]
    snapshot, explanation = provider.get_portfolio_margin_with_explanation(legs, clock=_clock())

    assert explanation.naked_exposure == 50 * 100.0
    assert explanation.covered_exposure == 0.0
    assert explanation.total_short_exposure == 50 * 100.0
    assert explanation.total_long_exposure == 0.0
    assert len(explanation.contributing_legs) == 1
    assert explanation.highest_margin_contributor.symbol == "NSE:NIFTY26AUG24800CE"
    assert "NAKED_SHORT_EXPOSURE" in explanation.risk_flags
    assert explanation.risk_classification == RISK_HIGH_NAKED_EXPOSURE


# --------------------------------------------------------------------- #
# Case 2 -- defined-risk spread
# --------------------------------------------------------------------- #

def test_defined_risk_spread_recognizes_hedge_and_lower_classification():
    provider = SimulatedMarginProvider()
    legs = [
        _leg("NSE:NIFTY26AUG24700CE", 50, 1, 120.0),   # long, fully covers the short below
        _leg("NSE:NIFTY26AUG24800CE", 50, -1, 80.0),    # short
    ]
    snapshot, explanation = provider.get_portfolio_margin_with_explanation(legs, clock=_clock())

    assert explanation.naked_exposure == 0.0   # long (6000) fully covers short (4000)
    assert explanation.covered_exposure == 50 * 80.0
    assert "NAKED_SHORT_EXPOSURE" not in explanation.risk_flags
    assert "HEDGE_PRESENT" in explanation.risk_flags
    assert explanation.risk_classification == RISK_DEFINED_RISK
    # DEFINED_RISK must classify strictly lower than a naked-short book of comparable size
    naked_snapshot, naked_explanation = provider.get_portfolio_margin_with_explanation(
        [_leg("NSE:NIFTY26AUG24800CE", 50, -1, 80.0)], clock=_clock(),
    )
    assert naked_explanation.risk_classification == RISK_HIGH_NAKED_EXPOSURE
    assert snapshot.required_margin < naked_snapshot.required_margin


# --------------------------------------------------------------------- #
# Case 3 -- short straddle
# --------------------------------------------------------------------- #

def test_short_straddle_both_legs_contribute_and_aggregate():
    provider = SimulatedMarginProvider()
    legs = [
        _leg("NSE:NIFTY26AUG24800CE", 50, -1, 90.0),
        _leg("NSE:NIFTY26AUG24800PE", 50, -1, 85.0),
    ]
    snapshot, explanation = provider.get_portfolio_margin_with_explanation(legs, clock=_clock())

    assert len(explanation.contributing_legs) == 2
    symbols = {c.symbol for c in explanation.contributing_legs}
    assert symbols == {"NSE:NIFTY26AUG24800CE", "NSE:NIFTY26AUG24800PE"}
    assert explanation.total_short_exposure == pytest.approx(50 * 90.0 + 50 * 85.0)
    assert explanation.risk_classification == RISK_HIGH_NAKED_EXPOSURE  # naked straddle, no hedge


# --------------------------------------------------------------------- #
# Integrity tests (7, 8, 9)
# --------------------------------------------------------------------- #

def test_missing_data_cannot_silently_create_approved_state():
    """Test 7: an invalid book must produce INVALID_STATE, never a
    valid-looking classification with fabricated numbers."""
    provider = SimulatedMarginProvider()
    legs = [MarginLegRequest(symbol="X", qty=50, side=-1, instrument_type="OPTIDX",
                              product_type="MIS", limit_price=None)]
    snapshot, explanation = provider.get_portfolio_margin_with_explanation(legs, clock=_clock())
    assert snapshot.margin_verified is False
    assert explanation.risk_classification == RISK_INVALID_STATE
    assert explanation.total_required_margin is None
    assert explanation.contributing_legs == ()
    assert explanation.highest_margin_contributor is None


def test_explanation_total_matches_snapshot_required_margin_exactly():
    """Test 8: the explanation can never disagree with its paired
    MarginSnapshot -- proven for several distinct book shapes, not
    just one."""
    provider = SimulatedMarginProvider()
    books = [
        [_leg("A", 50, -1, 100.0)],
        [_leg("A", 50, 1, 100.0)],
        [_leg("A", 50, -1, 80.0), _leg("B", 50, 1, 120.0)],
        [_leg("A", 50, -1, 90.0), _leg("B", 50, -1, 85.0)],
        [_leg("A", 75, -1, 60.0), _leg("B", 75, -1, 55.0), _leg("C", 75, 1, 20.0), _leg("D", 75, 1, 18.0)],
    ]
    for legs in books:
        snapshot, explanation = provider.get_portfolio_margin_with_explanation(legs, clock=_clock())
        assert snapshot.required_margin == pytest.approx(explanation.total_required_margin)
        # per-leg contributions must also sum EXACTLY to the book total, not just approximately match it
        contribution_sum = sum(c.margin_contribution for c in explanation.contributing_legs)
        assert contribution_sum == pytest.approx(explanation.total_required_margin)
        assert snapshot.margin_source == explanation.explanation_source


def test_explanation_and_snapshot_agree_on_validation_failure_too():
    provider = SimulatedMarginProvider()
    legs = [_leg("X", -5, -1, 90.0)]   # negative qty
    snapshot, explanation = provider.get_portfolio_margin_with_explanation(legs, clock=_clock())
    assert snapshot.margin_verified is False
    assert explanation.risk_classification == RISK_INVALID_STATE
    assert explanation.total_required_margin is None
    assert snapshot.required_margin is None


def test_changing_input_legs_changes_explanation_deterministically():
    """Test 9: different input -> different explanation, same input ->
    same explanation, every time."""
    provider = SimulatedMarginProvider()
    legs_a = [_leg("A", 50, -1, 100.0)]
    legs_b = [_leg("A", 100, -1, 100.0)]  # double the quantity

    _, explanation_a1 = provider.get_portfolio_margin_with_explanation(legs_a, clock=_clock())
    _, explanation_a2 = provider.get_portfolio_margin_with_explanation(legs_a, clock=_clock())
    _, explanation_b = provider.get_portfolio_margin_with_explanation(legs_b, clock=_clock())

    assert explanation_a1.total_required_margin == explanation_a2.total_required_margin  # same input, same output
    assert explanation_b.total_required_margin == pytest.approx(explanation_a1.total_required_margin * 2)
    assert explanation_a1.total_required_margin != explanation_b.total_required_margin


# --------------------------------------------------------------------- #
# classify_book_risk() as a standalone, independently testable function
# --------------------------------------------------------------------- #

def test_classify_book_risk_empty_book_is_low_risk():
    provider = SimulatedMarginProvider()
    snapshot, explanation = provider.get_portfolio_margin_with_explanation([], clock=_clock())
    assert explanation.risk_classification == RISK_LOW_RISK
    assert classify_book_risk(explanation) == RISK_LOW_RISK


def test_classify_book_risk_long_only_is_low_risk():
    provider = SimulatedMarginProvider()
    _, explanation = provider.get_portfolio_margin_with_explanation(
        [_leg("A", 50, 1, 100.0)], clock=_clock(),
    )
    assert explanation.risk_classification == RISK_LOW_RISK
    assert classify_book_risk(explanation) == RISK_LOW_RISK


def test_classify_book_risk_is_reusable_on_a_hand_built_explanation():
    """classify_book_risk operates on ANY MarginExplanation-shaped
    input, not just ones produced by SimulatedMarginProvider itself."""
    from bujji.trading_brain.risk_governor.simulated_margin_provider import MarginExplanation
    hand_built = MarginExplanation(
        total_required_margin=1000.0, total_long_exposure=0.0, total_short_exposure=500.0,
        covered_exposure=0.0, naked_exposure=500.0, contributing_legs=(),
        highest_margin_contributor=None, risk_flags=("NAKED_SHORT_EXPOSURE",),
        risk_classification="PLACEHOLDER", explanation_source="TEST",
    )
    assert classify_book_risk(hand_built) == RISK_HIGH_NAKED_EXPOSURE
