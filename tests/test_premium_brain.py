"""Premium Brain — unit tests (theta-only baseline correctness,
classification edge cases) + a real-data regression fixture."""
from datetime import datetime, timedelta

import pytest

from bujji.core.clock import IST
from bujji.core.enums import OptionType
from bujji.intelligence.models import DataQuality, PremiumBehavior
from bujji.intelligence.premium_brain import PremiumBrain
from bujji.intelligence.volatility_brain import _bs_price


@pytest.fixture
def brain():
    return PremiumBrain()


def _ts(hour, minute, day=20):
    return datetime(2026, 7, day, hour, minute, tzinfo=IST)


ENTRY_TIME = _ts(9, 20)
EXPIRY_TIME = datetime(2026, 7, 21, 15, 30, tzinfo=IST)


def _theoretical_combined(spot, strike, t_years, sigma, r=0.065):
    return (_bs_price(spot, strike, t_years, r, sigma, OptionType.CE)
            + _bs_price(spot, strike, t_years, r, sigma, OptionType.PE))


# ---------------------------------------------------------------------- #
# Data-quality gates -- never guess
# ---------------------------------------------------------------------- #
def test_missing_entry_iv_returns_unknown(brain):
    reading = brain.analyze(
        entry_combined_premium=300.0, current_combined_premium=280.0,
        spot_at_entry=24000, strike=24000, entry_iv=None,
        entry_time=ENTRY_TIME, now=_ts(10, 30), expiry_time=EXPIRY_TIME,
    )
    assert reading.behavior is PremiumBehavior.UNKNOWN
    assert reading.data_quality is DataQuality.INSUFFICIENT
    assert reading.confidence == 0.0
    assert "no_entry_iv" in reading.reason


def test_nonpositive_premium_returns_unknown(brain):
    reading = brain.analyze(
        entry_combined_premium=0.0, current_combined_premium=280.0,
        spot_at_entry=24000, strike=24000, entry_iv=0.15,
        entry_time=ENTRY_TIME, now=_ts(10, 30), expiry_time=EXPIRY_TIME,
    )
    assert reading.behavior is PremiumBehavior.UNKNOWN
    assert reading.data_quality is DataQuality.INSUFFICIENT
    assert "invalid_inputs" in reading.reason


def test_now_before_entry_returns_unknown(brain):
    reading = brain.analyze(
        entry_combined_premium=300.0, current_combined_premium=280.0,
        spot_at_entry=24000, strike=24000, entry_iv=0.15,
        entry_time=ENTRY_TIME, now=_ts(9, 0), expiry_time=EXPIRY_TIME,
    )
    assert reading.behavior is PremiumBehavior.UNKNOWN
    assert reading.data_quality is DataQuality.INSUFFICIENT


def test_at_or_past_expiry_returns_unknown(brain):
    reading = brain.analyze(
        entry_combined_premium=300.0, current_combined_premium=5.0,
        spot_at_entry=24000, strike=24000, entry_iv=0.15,
        entry_time=ENTRY_TIME, now=EXPIRY_TIME, expiry_time=EXPIRY_TIME,
    )
    assert reading.behavior is PremiumBehavior.UNKNOWN
    assert "theoretical_baseline_nonpositive" in reading.reason


# ---------------------------------------------------------------------- #
# Theta-only baseline correctness (exact synthetic control)
# ---------------------------------------------------------------------- #
def test_baseline_exactly_matches_bs_price_when_nothing_but_time_moved(brain):
    """If we feed the brain a 'current' premium computed from the exact
    same Black-Scholes formula it uses internally, with only time having
    moved forward, the ratio must be exactly 1.0 (DECAYING_AS_EXPECTED)."""
    spot, strike, sigma = 24000.0, 24000.0, 0.15
    now = _ts(11, 20)
    t_years_now = (EXPIRY_TIME - now).total_seconds() / (365 * 24 * 3600)
    exact_current = _theoretical_combined(spot, strike, t_years_now, sigma)
    t_years_entry = (EXPIRY_TIME - ENTRY_TIME).total_seconds() / (365 * 24 * 3600)
    entry_combined = _theoretical_combined(spot, strike, t_years_entry, sigma)

    reading = brain.analyze(
        entry_combined_premium=entry_combined, current_combined_premium=exact_current,
        spot_at_entry=spot, strike=strike, entry_iv=sigma,
        entry_time=ENTRY_TIME, now=now, expiry_time=EXPIRY_TIME,
    )
    assert reading.behavior is PremiumBehavior.DECAYING_AS_EXPECTED
    assert reading.behavior_ratio == pytest.approx(1.0, abs=1e-3)
    assert reading.data_quality is DataQuality.SUFFICIENT


def test_premium_far_below_theta_baseline_is_decaying_faster(brain):
    """Real premium well below the theta-only baseline -- something other
    than time (e.g. IV compression) accelerated the decay."""
    reading = brain.analyze(
        entry_combined_premium=400.0, current_combined_premium=100.0,
        spot_at_entry=24000, strike=24000, entry_iv=0.15,
        entry_time=ENTRY_TIME, now=_ts(11, 20), expiry_time=EXPIRY_TIME,
    )
    assert reading.behavior is PremiumBehavior.DECAYING_FASTER_THAN_THETA
    assert reading.behavior_ratio < 0.85


def test_premium_far_above_theta_baseline_is_rising_against_theta(brain):
    """Real premium well above the theta-only baseline -- something other
    than time (e.g. spot running away from the strike) is fighting decay."""
    reading = brain.analyze(
        entry_combined_premium=300.0, current_combined_premium=600.0,
        spot_at_entry=24000, strike=24000, entry_iv=0.15,
        entry_time=ENTRY_TIME, now=_ts(11, 20), expiry_time=EXPIRY_TIME,
    )
    assert reading.behavior is PremiumBehavior.RISING_AGAINST_THETA
    assert reading.behavior_ratio > 1.15


# ---------------------------------------------------------------------- #
# Derived fields
# ---------------------------------------------------------------------- #
def test_premium_captured_pct_and_time_elapsed_pct_are_sane(brain):
    reading = brain.analyze(
        entry_combined_premium=400.0, current_combined_premium=300.0,
        spot_at_entry=24000, strike=24000, entry_iv=0.15,
        entry_time=ENTRY_TIME, now=ENTRY_TIME + timedelta(hours=1), expiry_time=EXPIRY_TIME,
    )
    assert reading.premium_captured_pct == pytest.approx(25.0, abs=1e-6)
    assert reading.time_elapsed_pct is not None and 0 < reading.time_elapsed_pct < 5


def test_render_and_to_log_do_not_raise(brain):
    reading = brain.analyze(
        entry_combined_premium=400.0, current_combined_premium=300.0,
        spot_at_entry=24000, strike=24000, entry_iv=0.15,
        entry_time=ENTRY_TIME, now=_ts(11, 20), expiry_time=EXPIRY_TIME,
    )
    assert "PREMIUM BRAIN" in reading.render()
    log = reading.to_log()
    assert log["brain"] == "premium"


def test_render_handles_unknown_reading_without_raising(brain):
    reading = brain.analyze(
        entry_combined_premium=300.0, current_combined_premium=280.0,
        spot_at_entry=24000, strike=24000, entry_iv=None,
        entry_time=ENTRY_TIME, now=_ts(10, 30), expiry_time=EXPIRY_TIME,
    )
    assert "NOT AVAILABLE" in reading.render()


# ---------------------------------------------------------------------- #
# Real-data regression fixture (2026-07-13, live-fetched NIFTY CE/PE
# premiums -- see docs/AUDIT_LOG.md)
# ---------------------------------------------------------------------- #
def test_real_day_2026_07_13_09_20_to_10_30():
    """Regression fixture: real captured NIFTY 24000-strike combined
    premium at 09:20 entry (378.20) vs 10:30 (418.35), with spot rising
    from 24027.45 to 24154.75 over that window. Real IV solved directly
    from the real 09:20 premiums. Locks in the validated real-world
    result: ratio ~1.11, just under the RISING_AGAINST_THETA threshold,
    reflecting a real spot rally rather than decay-defying premium."""
    brain = PremiumBrain()
    entry_time = datetime(2026, 7, 13, 9, 20, tzinfo=IST)
    now = datetime(2026, 7, 13, 10, 30, tzinfo=IST)
    expiry = datetime(2026, 7, 21, 15, 30, tzinfo=IST)

    reading = brain.analyze(
        entry_combined_premium=378.20, current_combined_premium=418.35,
        spot_at_entry=24027.45, strike=24000, entry_iv=0.13032013344354126,
        entry_time=entry_time, now=now, expiry_time=expiry,
    )
    assert reading.data_quality is DataQuality.SUFFICIENT
    assert reading.behavior_ratio == pytest.approx(1.1095, abs=1e-2)
    assert reading.behavior is PremiumBehavior.DECAYING_AS_EXPECTED
    assert reading.premium_captured_pct == pytest.approx(-10.62, abs=1e-1)
