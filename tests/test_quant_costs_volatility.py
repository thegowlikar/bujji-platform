"""Tests for the cost model and the HAR-RV volatility layer.

The controls here guard the two ways this layer could manufacture a profit
that does not exist: filling at mid instead of crossing the spread, and
letting an unverified rate card pass as settled fact.
"""
from __future__ import annotations

import math
import random

import pytest

from bujji.quant_research.costs import (
    DEFAULT_RATE_CARD, FillModel, RateCard, REFUSE_NO_QUOTE,
    REFUSE_NONSENSE_QUOTE, SIDE_BUY, SIDE_SELL, cost_gate, fill_price,
    round_trip_cost)
from bujji.quant_research.volatility import (
    HORIZON_MONTHLY, MIN_OBSERVATIONS_TO_FIT, REFUSE_INSUFFICIENT_HISTORY,
    bipower_variation, fit_har, jump_component, realized_variance,
    variance_risk_premium)


def _har_series(n=900, seed=7, true=(0.0005, 0.35, 0.30, 0.25)):
    random.seed(seed)
    s = [0.01] * HORIZON_MONTHLY
    for _ in range(HORIZON_MONTHLY, n):
        d, w = s[-1], sum(s[-5:]) / 5
        m = sum(s[-HORIZON_MONTHLY:]) / HORIZON_MONTHLY
        s.append(max(1e-6, true[0] + true[1]*d + true[2]*w + true[3]*m
                     + random.gauss(0, 0.0004)))
    return s


# ---------------------------------------------------------------- costs
def test_taker_crosses_the_spread():
    buy = fill_price(100.0, 102.0, SIDE_BUY)
    sell = fill_price(100.0, 102.0, SIDE_SELL)
    assert buy["price"] == 102.0, "a buyer lifts the ask"
    assert sell["price"] == 100.0, "a seller hits the bid"
    assert buy["mid"] == 101.0


def test_no_quote_refuses_rather_than_using_ltp():
    r = fill_price(None, None, SIDE_BUY)
    assert r["price"] is None
    assert r["refused"] == REFUSE_NO_QUOTE
    assert "not a substitute" in r["detail"]


def test_crossed_book_refused():
    assert fill_price(105.0, 95.0, SIDE_BUY)["refused"] == REFUSE_NONSENSE_QUOTE


def test_round_trip_itemises_and_separates_spread_from_statutory():
    r = round_trip_cost(entry_bid=99.0, entry_ask=101.0, exit_bid=99.0,
                        exit_ask=101.0, lots=1, lot_size=75, side=SIDE_BUY)
    assert r["refused"] is None
    assert r["spread_cost"] > 0
    assert r["statutory"]["subtotal"] > 0
    assert math.isclose(r["total"],
                        r["spread_cost"] + r["statutory"]["subtotal"])


def test_cheap_options_are_dominated_by_friction():
    """The documented retail trap, reproduced."""
    cheap = round_trip_cost(entry_bid=1.4, entry_ask=1.6, exit_bid=1.4,
                            exit_ask=1.6, lots=1, lot_size=75, side=SIDE_BUY)
    rich = round_trip_cost(entry_bid=299.0, entry_ask=301.0, exit_bid=299.0,
                           exit_ask=301.0, lots=1, lot_size=75, side=SIDE_BUY)
    assert (cheap["breakeven_as_fraction_of_entry"]
            > 10 * rich["breakeven_as_fraction_of_entry"])
    assert cost_gate(cheap)["verdict"] == "FRICTION_DOMINATES"
    assert cost_gate(rich)["verdict"] == "ACCEPTABLE_FRICTION"


def test_default_rate_card_is_unverified():
    assert DEFAULT_RATE_CARD.verified is False
    r = round_trip_cost(entry_bid=99.0, entry_ask=101.0, exit_bid=99.0,
                        exit_ask=101.0, lots=1, lot_size=75, side=SIDE_BUY)
    assert r["rates_verified"] is False
    assert "UNVERIFIED" in r["warning"]


def test_gate_says_nothing_about_edge():
    r = round_trip_cost(entry_bid=299.0, entry_ask=301.0, exit_bid=299.0,
                        exit_ask=301.0, lots=1, lot_size=75, side=SIDE_BUY)
    assert "says nothing about whether the trade has an edge" in cost_gate(r)["note"]


# ----------------------------------------------------------- volatility
def test_har_recovers_known_coefficients():
    """Without this the refusal tests below would be vacuous."""
    out = fit_har(_har_series())
    m = out["model"]
    assert out["refused"] is None
    assert abs(m.beta_daily - 0.35) < 0.10
    assert abs(m.beta_weekly - 0.30) < 0.12
    assert abs(m.beta_monthly - 0.25) < 0.12
    assert m.r_squared > 0.5


def test_har_refuses_on_one_session():
    out = fit_har([0.16])
    assert out["model"] is None
    assert out["refused"] == REFUSE_INSUFFICIENT_HISTORY
    assert out["have"] == 1


def test_har_refuses_just_below_the_threshold():
    """The boundary, not just the obvious case."""
    need = MIN_OBSERVATIONS_TO_FIT + HORIZON_MONTHLY
    assert fit_har(_har_series(n=need + 5)[:need - 1])["refused"] == (
        REFUSE_INSUFFICIENT_HISTORY)
    assert fit_har(_har_series(n=need + 60))["refused"] is None


def test_forecast_states_that_in_sample_r2_is_not_skill():
    m = fit_har(_har_series())["model"]
    f = m.forecast(0.01, 0.01, 0.01)
    assert "not forecast skill" in f["limit"]


def test_bipower_isolates_a_jump():
    smooth = [100 * math.exp(0.0005 * i) for i in range(60)]
    jumpy = list(smooth)
    jumpy[30] *= 1.05
    assert jump_component(smooth)["jump_share"] == 0.0
    assert jump_component(jumpy)["jump_share"] > 0.10


def test_realized_variance_never_bridges_a_gap():
    """A missing observation must not become a return.

    Bridging 100 -> 101 across an absent price would attribute a move to an
    interval nobody observed. Both pairs touch the gap, so both are dropped
    and the result is None -- absence propagating rather than being filled in.
    """
    assert realized_variance([100.0, None, 101.0]) is None
    assert realized_variance([100.0]) is None
    # a clean series still works, so the check above is not vacuous
    assert realized_variance([100.0, 101.0, 102.0]) > 0


# ==================================================== NEGATIVE CONTROLS
def test_control_11_mid_fill_manufactures_profit():
    """Filling at mid awards half the spread twice. It must be visible."""
    taker = round_trip_cost(entry_bid=99.0, entry_ask=101.0, exit_bid=99.0,
                            exit_ask=101.0, lots=1, lot_size=75,
                            side=SIDE_BUY, model=FillModel(cross_the_spread=True))
    assert taker["spread_cost"] > 0, "control anchor: a taker pays the spread"

    optimistic = round_trip_cost(entry_bid=99.0, entry_ask=101.0, exit_bid=99.0,
                                 exit_ask=101.0, lots=1, lot_size=75,
                                 side=SIDE_BUY,
                                 model=FillModel(cross_the_spread=False))
    assert optimistic["spread_cost"] == 0
    assert optimistic["total"] < taker["total"]
    assert "MID FILL ASSUMED" in optimistic["fill_basis"], (
        "a mid fill must announce itself; silently free spread is how a "
        "backtest invents an edge")


def test_control_12_unverified_rates_cannot_pass_as_verified():
    unverified = round_trip_cost(entry_bid=99.0, entry_ask=101.0, exit_bid=99.0,
                                 exit_ask=101.0, lots=1, lot_size=75,
                                 side=SIDE_BUY)
    assert unverified["warning"] is not None
    assert unverified["rates_verified"] is False

    verified = round_trip_cost(
        entry_bid=99.0, entry_ask=101.0, exit_bid=99.0, exit_ask=101.0,
        lots=1, lot_size=75, side=SIDE_BUY,
        rates=RateCard(effective_from="2026-08-25", verified=True,
                       source_note="checked against contract note"))
    assert verified["warning"] is None, "control anchor: a verified card is clean"


def test_control_13_vrp_computed_backwards_is_named():
    forward = variance_risk_premium(0.20, 0.15, implied_ts=100.0,
                                    realized_window_end_ts=200.0)
    assert forward["usable_as_premium"] is True, "control anchor"

    backwards = variance_risk_premium(0.20, 0.15, implied_ts=300.0,
                                      realized_window_end_ts=200.0)
    assert backwards["ordering"] == "BACKWARDS_NOT_A_PREMIUM"
    assert backwards["usable_as_premium"] is False


def test_control_14_single_vrp_observation_is_not_a_premium():
    v = variance_risk_premium(0.20, 0.15, implied_ts=1.0,
                              realized_window_end_ts=2.0)
    assert "one pair is an observation, not a premium" in v["limit"]
    assert "not established" in v["limit"]
