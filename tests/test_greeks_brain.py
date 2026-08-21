"""Greeks Brain — unit tests (formula correctness via finite-difference
cross-checks against the already-validated Black-Scholes pricer,
classification edge cases) + a real-data regression fixture."""
from datetime import datetime

import pytest

from bujji.core.clock import IST
from bujji.core.enums import OptionType
from bujji.intelligence.context import IntelligenceContext
from bujji.intelligence.greeks_brain import GreeksBrain, _bs_delta, _bs_gamma, _bs_theta
from bujji.intelligence.models import DataQuality, GreeksExposure
from bujji.intelligence.volatility_brain import _bs_price, _bs_vega

TEST_CONTEXT = IntelligenceContext(as_of_time=datetime(2026, 7, 20, 9, 20, tzinfo=IST))

SPOT, STRIKE, T_YEARS, R, SIGMA = 24000.0, 24000.0, 5 / 365, 0.065, 0.15
EXPOSURE_THRESHOLD_SANITY = 0.15


@pytest.fixture
def brain():
    return GreeksBrain()


# ---------------------------------------------------------------------- #
# Formula correctness: finite-difference cross-check against the pricer
# already validated by the Volatility Brain's solver round-trip test.
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize("option_type", [OptionType.CE, OptionType.PE])
def test_delta_matches_finite_difference(option_type):
    h = 0.01
    p1 = _bs_price(SPOT, STRIKE, T_YEARS, R, SIGMA, option_type)
    p2 = _bs_price(SPOT + h, STRIKE, T_YEARS, R, SIGMA, option_type)
    fd_delta = (p2 - p1) / h
    analytic_delta = _bs_delta(SPOT, STRIKE, T_YEARS, R, SIGMA, option_type)
    assert analytic_delta == pytest.approx(fd_delta, abs=1e-3)


def test_gamma_matches_finite_difference_of_delta():
    h = 0.01
    d1 = _bs_delta(SPOT, STRIKE, T_YEARS, R, SIGMA, OptionType.CE)
    d2 = _bs_delta(SPOT + h, STRIKE, T_YEARS, R, SIGMA, OptionType.CE)
    fd_gamma = (d2 - d1) / h
    analytic_gamma = _bs_gamma(SPOT, STRIKE, T_YEARS, R, SIGMA)
    assert analytic_gamma == pytest.approx(fd_gamma, abs=1e-5)


@pytest.mark.parametrize("option_type", [OptionType.CE, OptionType.PE])
def test_theta_matches_instantaneous_finite_difference(option_type):
    """A full 1-day finite-difference step has real discretization error
    against a 5-day-to-expiry position (nonlinear time decay curve) --
    cross-check instead with a tiny time step, which converges on the
    true instantaneous rate the analytic formula computes."""
    h = 1e-5
    p1 = _bs_price(SPOT, STRIKE, T_YEARS, R, SIGMA, option_type)
    p2 = _bs_price(SPOT, STRIKE, T_YEARS - h, R, SIGMA, option_type)
    # p2 is the price after h years of calendar time have passed (time
    # remaining shrank by h) -- (p2 - p1) / h is the rate of value change
    # per unit calendar time, i.e. exactly what theta represents (negative
    # for a long option: value falls as time passes).
    fd_theta_per_year = (p2 - p1) / h
    fd_theta_per_day = fd_theta_per_year / 365.0
    analytic_theta_per_day = _bs_theta(SPOT, STRIKE, T_YEARS, R, SIGMA, option_type) / 365.0
    assert analytic_theta_per_day == pytest.approx(fd_theta_per_day, abs=0.05)


def test_vega_matches_finite_difference():
    h = 1e-5
    p1 = _bs_price(SPOT, STRIKE, T_YEARS, R, SIGMA, OptionType.CE)
    p2 = _bs_price(SPOT, STRIKE, T_YEARS, R, SIGMA + h, OptionType.CE)
    fd_vega_per_pct = (p2 - p1) / h * 0.01
    analytic_vega_per_pct = _bs_vega(SPOT, STRIKE, T_YEARS, R, SIGMA) / 100.0
    assert analytic_vega_per_pct == pytest.approx(fd_vega_per_pct, abs=1e-2)


# ---------------------------------------------------------------------- #
# Known ATM sanity: call delta ~ +0.5, put delta ~ -0.5, gamma positive.
# ---------------------------------------------------------------------- #
def test_atm_deltas_are_near_half(brain):
    reading = brain.analyze(spot=SPOT, strike=STRIKE, t_years=T_YEARS, iv_ce=SIGMA, iv_pe=SIGMA, context=TEST_CONTEXT)
    assert 0.45 < reading.delta_ce < 0.55
    assert -0.55 < reading.delta_pe < -0.45


def test_gammas_are_positive_and_equal_for_same_iv(brain):
    reading = brain.analyze(spot=SPOT, strike=STRIKE, t_years=T_YEARS, iv_ce=SIGMA, iv_pe=SIGMA, context=TEST_CONTEXT)
    assert reading.gamma_ce > 0
    assert reading.gamma_pe == pytest.approx(reading.gamma_ce, abs=1e-9)


def test_per_leg_theta_is_negative_long_position_decays(brain):
    reading = brain.analyze(spot=SPOT, strike=STRIKE, t_years=T_YEARS, iv_ce=SIGMA, iv_pe=SIGMA, context=TEST_CONTEXT)
    assert reading.theta_ce_per_day < 0
    assert reading.theta_pe_per_day < 0


def test_position_theta_is_positive_seller_collects_decay(brain):
    """The SHORT straddle is the mirror image of the per-leg long
    Greeks -- position theta must be positive (seller earns from time
    passing), matching the entire premise of this strategy."""
    reading = brain.analyze(spot=SPOT, strike=STRIKE, t_years=T_YEARS, iv_ce=SIGMA, iv_pe=SIGMA, context=TEST_CONTEXT)
    assert reading.position_theta_per_day > 0


def test_atm_straddle_is_near_delta_neutral(brain):
    reading = brain.analyze(spot=SPOT, strike=STRIKE, t_years=T_YEARS, iv_ce=SIGMA, iv_pe=SIGMA, context=TEST_CONTEXT)
    assert reading.exposure is GreeksExposure.DELTA_NEUTRAL


def test_spot_well_above_strike_gives_net_short_exposure(brain):
    """Spot has run well above the strike -- the short straddle now
    behaves like a net short call, i.e. hurt further by more upside."""
    reading = brain.analyze(spot=24500, strike=24000, t_years=T_YEARS, iv_ce=SIGMA, iv_pe=SIGMA, context=TEST_CONTEXT)
    assert reading.position_delta < -EXPOSURE_THRESHOLD_SANITY
    assert reading.exposure is GreeksExposure.NET_SHORT_EXPOSURE


def test_spot_well_below_strike_gives_net_long_exposure(brain):
    reading = brain.analyze(spot=23500, strike=24000, t_years=T_YEARS, iv_ce=SIGMA, iv_pe=SIGMA, context=TEST_CONTEXT)
    assert reading.position_delta > EXPOSURE_THRESHOLD_SANITY
    assert reading.exposure is GreeksExposure.NET_LONG_EXPOSURE


# ---------------------------------------------------------------------- #
# Data-quality gates -- never guess
# ---------------------------------------------------------------------- #
def test_missing_iv_returns_unknown(brain):
    reading = brain.analyze(spot=SPOT, strike=STRIKE, t_years=T_YEARS, iv_ce=None, iv_pe=SIGMA, context=TEST_CONTEXT)
    assert reading.exposure is GreeksExposure.UNKNOWN
    assert reading.data_quality is DataQuality.INSUFFICIENT
    assert "missing_iv" in reading.reason


def test_nonpositive_time_returns_unknown(brain):
    reading = brain.analyze(spot=SPOT, strike=STRIKE, t_years=0.0, iv_ce=SIGMA, iv_pe=SIGMA, context=TEST_CONTEXT)
    assert reading.exposure is GreeksExposure.UNKNOWN
    assert "invalid_time" in reading.reason


def test_nonpositive_iv_returns_unknown(brain):
    reading = brain.analyze(spot=SPOT, strike=STRIKE, t_years=T_YEARS, iv_ce=0.0, iv_pe=SIGMA, context=TEST_CONTEXT)
    assert reading.exposure is GreeksExposure.UNKNOWN
    assert "invalid_iv" in reading.reason


def test_render_and_to_log_do_not_raise(brain):
    reading = brain.analyze(spot=SPOT, strike=STRIKE, t_years=T_YEARS, iv_ce=SIGMA, iv_pe=SIGMA, context=TEST_CONTEXT)
    assert "GREEKS BRAIN" in reading.render()
    log = reading.to_log()
    assert log["brain"] == "greeks"


def test_render_handles_unknown_reading_without_raising(brain):
    reading = brain.analyze(spot=SPOT, strike=STRIKE, t_years=T_YEARS, iv_ce=None, iv_pe=SIGMA, context=TEST_CONTEXT)
    assert "NOT AVAILABLE" in reading.render()


# ---------------------------------------------------------------------- #
# Real-data regression fixture (2026-07-13, real spot/strike/IV -- see
# docs/AUDIT_LOG.md; same entry point used by the Premium Brain fixture)
# ---------------------------------------------------------------------- #
def test_real_day_2026_07_13_09_20_entry_greeks():
    """Regression fixture: real 24000-strike straddle at the real
    09:20 entry on 2026-07-13, with the real solved entry IVs for each
    leg. Locks in a near-delta-neutral ATM entry, as expected for an
    ATM straddle sold right at the money."""
    brain = GreeksBrain()
    entry_time = datetime(2026, 7, 13, 9, 20, tzinfo=IST)
    expiry_time = datetime(2026, 7, 21, 15, 30, tzinfo=IST)
    t_years_entry = (expiry_time - entry_time).total_seconds() / (365 * 24 * 3600)

    reading = brain.analyze(
        spot=24027.45, strike=24000, t_years=t_years_entry,
        iv_ce=0.11675046927166245, iv_pe=0.14388979761542006,
     context=TEST_CONTEXT)
    assert reading.data_quality is DataQuality.SUFFICIENT
    assert reading.exposure is GreeksExposure.DELTA_NEUTRAL
    assert reading.position_theta_per_day > 0
