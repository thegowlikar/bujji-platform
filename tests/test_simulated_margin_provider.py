"""Tests — Numeric Risk Governor Gate C.1 (deterministic simulated
margin provider). Zero network access anywhere in this file."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bujji.trading_brain.risk_governor.simulated_margin_provider import (
    HEDGED_MARGIN_RATE,
    LONG_MARGIN_RATE,
    SHORT_MARGIN_RATE,
    SIMULATED_MARGIN_SOURCE,
    SIMULATED_VALIDATION_FAILED_SOURCE,
    SimulatedMarginProvider,
)
from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginLegRequest


def _clock(iso="2026-08-02T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _leg(symbol, qty, side, price, instrument_type="OPTIDX", product_type="MIS"):
    return MarginLegRequest(symbol=symbol, qty=qty, side=side, instrument_type=instrument_type,
                             product_type=product_type, limit_price=price)


# --------------------------------------------------------------------- #
# Case A -- empty book
# --------------------------------------------------------------------- #

def test_empty_book_zero_margin_verified():
    provider = SimulatedMarginProvider()
    snapshot = provider.get_portfolio_margin([], clock=_clock())
    assert snapshot.required_margin == 0.0
    assert snapshot.margin_verified is True
    assert snapshot.margin_source == SIMULATED_MARGIN_SOURCE


def test_determinism_same_input_same_output():
    provider = SimulatedMarginProvider()
    legs = [_leg("A", 50, -1, 100.0)]
    s1 = provider.get_portfolio_margin(legs, clock=_clock())
    s2 = provider.get_portfolio_margin(legs, clock=_clock())
    assert s1.required_margin == s2.required_margin
    assert s1.margin_verified == s2.margin_verified


# --------------------------------------------------------------------- #
# Case B -- single naked short option
# --------------------------------------------------------------------- #

def test_naked_short_option_positive_margin():
    provider = SimulatedMarginProvider()
    legs = [_leg("NSE:NIFTY26AUG24800CE", 50, -1, 100.0)]
    snapshot = provider.get_portfolio_margin(legs, clock=_clock())
    assert snapshot.margin_verified is True
    assert snapshot.required_margin == SHORT_MARGIN_RATE * (50 * 100.0)
    assert snapshot.required_margin > 0


# --------------------------------------------------------------------- #
# Case C -- long option only
# --------------------------------------------------------------------- #

def test_long_option_only_lower_margin_than_naked_short_same_notional():
    provider = SimulatedMarginProvider()
    long_legs = [_leg("NSE:NIFTY26AUG24800CE", 50, 1, 100.0)]
    short_legs = [_leg("NSE:NIFTY26AUG24800CE", 50, -1, 100.0)]
    long_snapshot = provider.get_portfolio_margin(long_legs, clock=_clock())
    short_snapshot = provider.get_portfolio_margin(short_legs, clock=_clock())
    assert long_snapshot.required_margin == LONG_MARGIN_RATE * (50 * 100.0)
    assert long_snapshot.required_margin < short_snapshot.required_margin


# --------------------------------------------------------------------- #
# Case D -- bull call spread (defined-risk) vs naked short of the same leg
# --------------------------------------------------------------------- #

def test_bull_call_spread_lower_margin_than_naked_short_call():
    provider = SimulatedMarginProvider()
    spread_legs = [
        _leg("NSE:NIFTY26AUG24700CE", 50, 1, 120.0),   # buy lower strike (long)
        _leg("NSE:NIFTY26AUG24800CE", 50, -1, 80.0),    # sell higher strike (short)
    ]
    naked_short_legs = [_leg("NSE:NIFTY26AUG24800CE", 50, -1, 80.0)]  # the short leg alone, naked
    spread_snapshot = provider.get_portfolio_margin(spread_legs, clock=_clock())
    naked_snapshot = provider.get_portfolio_margin(naked_short_legs, clock=_clock())
    assert spread_snapshot.margin_verified is True
    assert spread_snapshot.required_margin < naked_snapshot.required_margin


def test_bull_call_spread_margin_computed_correctly():
    provider = SimulatedMarginProvider()
    legs = [
        _leg("NSE:NIFTY26AUG24700CE", 50, 1, 120.0),
        _leg("NSE:NIFTY26AUG24800CE", 50, -1, 80.0),
    ]
    snapshot = provider.get_portfolio_margin(legs, clock=_clock())
    short_notional, long_notional = 50 * 80.0, 50 * 120.0
    covered = min(short_notional, long_notional)
    naked = short_notional - covered
    expected = SHORT_MARGIN_RATE * naked + HEDGED_MARGIN_RATE * covered + LONG_MARGIN_RATE * long_notional
    assert snapshot.required_margin == pytest.approx(expected)


# --------------------------------------------------------------------- #
# Case E -- short straddle (CE + PE), combined aggregation
# --------------------------------------------------------------------- #

def test_short_straddle_combines_ce_and_pe_margin():
    provider = SimulatedMarginProvider()
    single_leg = [_leg("NSE:NIFTY26AUG24800CE", 50, -1, 90.0)]
    straddle_legs = [
        _leg("NSE:NIFTY26AUG24800CE", 50, -1, 90.0),
        _leg("NSE:NIFTY26AUG24800PE", 50, -1, 85.0),
    ]
    single_snapshot = provider.get_portfolio_margin(single_leg, clock=_clock())
    straddle_snapshot = provider.get_portfolio_margin(straddle_legs, clock=_clock())
    assert straddle_snapshot.required_margin > single_snapshot.required_margin
    assert straddle_snapshot.required_margin == pytest.approx(
        SHORT_MARGIN_RATE * (50 * 90.0 + 50 * 85.0)
    )


# --------------------------------------------------------------------- #
# Case F -- multiple position groups (whole-book aggregation)
# --------------------------------------------------------------------- #

def test_multiple_groups_whole_book_aggregation_not_single_trade():
    """The provider receives a flat List[MarginLegRequest] -- it has no
    concept of 'which position group' a leg came from, so aggregation
    across groups is naturally whole-book, never accidentally
    collapsed to a single trade's legs."""
    provider = SimulatedMarginProvider()
    group_a_legs = [_leg("NSE:NIFTY26AUG24800CE", 50, -1, 90.0)]
    group_b_legs = [_leg("NSE:NIFTY26AUG25000PE", 50, -1, 70.0)]
    whole_book_legs = group_a_legs + group_b_legs

    snapshot_a = provider.get_portfolio_margin(group_a_legs, clock=_clock())
    snapshot_b = provider.get_portfolio_margin(group_b_legs, clock=_clock())
    snapshot_whole_book = provider.get_portfolio_margin(whole_book_legs, clock=_clock())

    assert snapshot_whole_book.required_margin == pytest.approx(
        snapshot_a.required_margin + snapshot_b.required_margin
    )
    assert snapshot_whole_book.required_margin > snapshot_a.required_margin
    assert snapshot_whole_book.required_margin > snapshot_b.required_margin


# --------------------------------------------------------------------- #
# Case G -- invalid requests, deterministic validation failure
# --------------------------------------------------------------------- #

def test_empty_symbol_fails_validation():
    provider = SimulatedMarginProvider()
    legs = [_leg("", 50, -1, 90.0)]
    snapshot = provider.get_portfolio_margin(legs, clock=_clock())
    assert snapshot.margin_verified is False
    assert snapshot.required_margin is None
    assert snapshot.margin_source == SIMULATED_VALIDATION_FAILED_SOURCE


def test_whitespace_only_symbol_fails_validation():
    """Adversarial audit finding: a non-empty but whitespace-only
    symbol ('   ') is truthy in Python, so a naive `if not leg.symbol`
    check alone lets it through -- this is exactly the 'missing
    metadata creates a fake valid margin request' failure mode, since
    a whitespace-only string is not a real, usable instrument
    identifier any more than an empty one is."""
    provider = SimulatedMarginProvider()
    legs = [_leg("   ", 50, -1, 90.0)]
    snapshot = provider.get_portfolio_margin(legs, clock=_clock())
    assert snapshot.margin_verified is False
    assert snapshot.margin_source == SIMULATED_VALIDATION_FAILED_SOURCE


def test_invalid_side_fails_validation():
    provider = SimulatedMarginProvider()
    legs = [MarginLegRequest(symbol="X", qty=50, side=0, instrument_type="OPTIDX",
                              product_type="MIS", limit_price=90.0)]
    snapshot = provider.get_portfolio_margin(legs, clock=_clock())
    assert snapshot.margin_verified is False
    assert snapshot.margin_source == SIMULATED_VALIDATION_FAILED_SOURCE


def test_negative_quantity_fails_validation():
    provider = SimulatedMarginProvider()
    legs = [_leg("X", -50, -1, 90.0)]
    snapshot = provider.get_portfolio_margin(legs, clock=_clock())
    assert snapshot.margin_verified is False
    assert snapshot.margin_source == SIMULATED_VALIDATION_FAILED_SOURCE


def test_zero_quantity_fails_validation():
    provider = SimulatedMarginProvider()
    legs = [_leg("X", 0, -1, 90.0)]
    snapshot = provider.get_portfolio_margin(legs, clock=_clock())
    assert snapshot.margin_verified is False


def test_missing_reference_price_fails_validation():
    provider = SimulatedMarginProvider()
    legs = [MarginLegRequest(symbol="X", qty=50, side=-1, instrument_type="OPTIDX",
                              product_type="MIS", limit_price=None)]
    snapshot = provider.get_portfolio_margin(legs, clock=_clock())
    assert snapshot.margin_verified is False
    assert snapshot.required_margin is None


def test_negative_reference_price_fails_validation():
    provider = SimulatedMarginProvider()
    legs = [_leg("X", 50, -1, -10.0)]
    snapshot = provider.get_portfolio_margin(legs, clock=_clock())
    assert snapshot.margin_verified is False


def test_one_invalid_leg_among_valid_legs_fails_the_whole_batch():
    """A single malformed leg must not be silently dropped while the
    rest of the book is priced -- that would understate the true
    whole-book requirement, the same principle already established for
    project_whole_book_to_margin_legs."""
    provider = SimulatedMarginProvider()
    legs = [
        _leg("VALID-1", 50, -1, 90.0),
        MarginLegRequest(symbol="INVALID", qty=50, side=-1, instrument_type="OPTIDX",
                          product_type="MIS", limit_price=None),
    ]
    snapshot = provider.get_portfolio_margin(legs, clock=_clock())
    assert snapshot.margin_verified is False
    assert snapshot.required_margin is None
