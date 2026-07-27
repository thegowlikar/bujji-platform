"""Volatility Brain — unit tests (solver correctness, classification edge
cases) + real-data regression fixtures (locking in validated behavior
against genuine NIFTY option premiums fetched live during this session).
"""
from datetime import datetime, timedelta

import pytest

from bujji.core.clock import IST
from bujji.core.enums import OptionType
from bujji.core.models import Candle
from bujji.intelligence.models import DataQuality, Richness
from bujji.intelligence.volatility_brain import (
    MIN_CANDLES_FOR_REALIZED_VOL,
    VolatilityBrain,
    _bs_price,
    solve_implied_volatility,
)


def _candles(closes: list[float], start_hour=9, start_min=15) -> list[Candle]:
    out = []
    for i, c in enumerate(closes):
        ts = datetime(2026, 7, 20, start_hour, start_min, tzinfo=IST) + timedelta(minutes=5 * i)
        out.append(Candle(ts, c, c + 1, c - 1, c, 1000))
    return out


@pytest.fixture
def brain():
    return VolatilityBrain()


# ---------------------------------------------------------------------- #
# Solver correctness (must exactly recover a known sigma via round-trip)
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize("true_sigma", [0.08, 0.10, 0.15, 0.20, 0.30, 0.50])
def test_solver_round_trips_exactly_on_synthetic_known_sigma(true_sigma):
    price = _bs_price(24000, 24000, 5 / 365, 0.065, true_sigma, OptionType.CE)
    solved = solve_implied_volatility(price, 24000, 24000, 5 / 365, 0.065, OptionType.CE)
    assert solved == pytest.approx(true_sigma, abs=1e-4)


def test_solver_round_trips_for_puts_too():
    price = _bs_price(24000, 24200, 5 / 365, 0.065, 0.18, OptionType.PE)
    solved = solve_implied_volatility(price, 24000, 24200, 5 / 365, 0.065, OptionType.PE)
    assert solved == pytest.approx(0.18, abs=1e-4)


def test_solver_returns_none_for_price_below_intrinsic():
    """A price below intrinsic value is not a solvable/trustworthy quote --
    must refuse, never guess."""
    # Deep ITM CE: intrinsic = 24500 - 24000 = 500, quoting 100 is impossible.
    solved = solve_implied_volatility(100.0, 24500, 24000, 5 / 365, 0.065, OptionType.CE)
    assert solved is None


def test_solver_returns_none_for_nonpositive_time_or_price():
    assert solve_implied_volatility(100.0, 24000, 24000, 0.0, 0.065, OptionType.CE) is None
    assert solve_implied_volatility(0.0, 24000, 24000, 5 / 365, 0.065, OptionType.CE) is None
    assert solve_implied_volatility(-5.0, 24000, 24000, 5 / 365, 0.065, OptionType.CE) is None


# ---------------------------------------------------------------------- #
# Data-quality gate
# ---------------------------------------------------------------------- #
def test_insufficient_candles_returns_unknown_never_guesses(brain):
    candles = _candles([24000, 24005, 24010])  # Fewer than MIN_CANDLES.
    reading = brain.analyze(candles, 24000, 24000, 5 / 365, 150.0, 150.0)
    assert reading.richness is Richness.UNKNOWN
    assert reading.confidence == 0.0
    assert reading.data_quality is DataQuality.INSUFFICIENT
    assert "insufficient_data" in reading.reason
    assert reading.iv_rank is None
    assert reading.iv_percentile is None


def test_unsolvable_premium_produces_unknown_not_a_crash(brain):
    """A premium that can't be solved (e.g. below intrinsic) must degrade
    to UNKNOWN richness, not raise or fabricate a number."""
    candles = _candles([24000 + i for i in range(MIN_CANDLES_FOR_REALIZED_VOL)])
    reading = brain.analyze(candles, 24500, 24000, 5 / 365, 1.0, 150.0)  # CE price impossible.
    assert reading.iv_ce is None
    assert reading.richness is Richness.UNKNOWN
    assert reading.data_quality is DataQuality.INSUFFICIENT


# ---------------------------------------------------------------------- #
# IV rank/percentile are ALWAYS None (documented data limitation)
# ---------------------------------------------------------------------- #
def test_iv_rank_and_percentile_are_always_none(brain):
    candles = _candles([24000 + i * 2 for i in range(10)])
    price = _bs_price(24000, 24000, 5 / 365, 0.065, 0.15, OptionType.CE)
    reading = brain.analyze(candles, 24000, 24000, 5 / 365, price, price)
    assert reading.iv_rank is None
    assert reading.iv_percentile is None


# ---------------------------------------------------------------------- #
# Richness classification (synthetic, exact IV vs RV control)
# ---------------------------------------------------------------------- #
def test_high_iv_relative_to_realized_vol_is_rich(brain):
    # Flat/quiet spot (near-zero realized vol) + a premium implying real IV
    # -> IV massively exceeds realized vol -> unambiguously rich.
    candles = _candles([24000.0, 24000.5, 24000.0, 24000.5, 24000.0, 24000.5])
    ce_price = _bs_price(24000, 24000, 5 / 365, 0.065, 0.20, OptionType.CE)
    pe_price = _bs_price(24000, 24000, 5 / 365, 0.065, 0.20, OptionType.PE)
    reading = brain.analyze(candles, 24000, 24000, 5 / 365, ce_price, pe_price)
    assert reading.richness is Richness.IV_RICH
    assert reading.richness_ratio > 1.15


def test_low_iv_relative_to_realized_vol_is_cheap(brain):
    # Wildly choppy real spot (high realized vol) + a premium implying LOW
    # IV -> IV well below realized vol -> unambiguously cheap.
    candles = _candles([24000, 24300, 23700, 24400, 23600, 24500])
    ce_price = _bs_price(24000, 24000, 5 / 365, 0.065, 0.05, OptionType.CE)
    pe_price = _bs_price(24000, 24000, 5 / 365, 0.065, 0.05, OptionType.PE)
    reading = brain.analyze(candles, 24000, 24000, 5 / 365, ce_price, pe_price)
    assert reading.richness is Richness.IV_CHEAP
    assert reading.richness_ratio < 0.85


def test_expected_move_is_computed_and_positive(brain):
    candles = _candles([24000 + i for i in range(10)])
    ce_price = _bs_price(24000, 24000, 5 / 365, 0.065, 0.15, OptionType.CE)
    pe_price = _bs_price(24000, 24000, 5 / 365, 0.065, 0.15, OptionType.PE)
    reading = brain.analyze(candles, 24000, 24000, 5 / 365, ce_price, pe_price)
    assert reading.expected_move_points is not None
    assert reading.expected_move_points > 0
    assert reading.expected_move_pct > 0


def test_render_and_to_log_do_not_raise(brain):
    candles = _candles([24000 + i for i in range(10)])
    reading = brain.analyze(candles, 24000, 24000, 5 / 365, 150.0, 150.0)
    assert "VOLATILITY BRAIN" in reading.render()
    log = reading.to_log()
    assert log["brain"] == "volatility"


# ---------------------------------------------------------------------- #
# Real-data regression fixture (2026-07-13, live-fetched NIFTY option
# premiums and spot -- see docs/AUDIT_LOG.md)
# ---------------------------------------------------------------------- #
def test_real_day_2026_07_13_classifies_as_iv_rich():
    """Regression fixture: real captured NIFTY 24000-strike CE/PE premiums
    and real spot at 10:30 on 2026-07-13 -- validated live during this
    codebase's development history to produce IV_RICH with IV/RV ~1.7."""
    brain = VolatilityBrain()
    # Exact real spot closes, 5-min candles, session start through 10:30
    # (re-fetched from /tmp/nifty_real_spot_20260701_20260719.json on the
    # VPS to replace an earlier down-sampled/non-matching approximation).
    spot_closes = [
        24027.45, 24023.5, 24027.45, 24028.05, 24067.95, 24075.0, 24107.45,
        24116.0, 24121.2, 24110.1, 24109.3, 24114.15, 24113.8, 24119.1,
        24145.8, 24154.75,
    ]
    candles = _candles(spot_closes)
    reading = brain.analyze(
        candles, spot=24154.8, strike=24000, t_years=(21 - 13) * 86400 / (365 * 86400),
        ce_premium=282.85, pe_premium=135.5,
    )
    assert reading.data_quality is DataQuality.SUFFICIENT
    assert reading.richness is Richness.IV_RICH
    assert reading.iv_average is not None and reading.iv_average > 0
    assert reading.expected_move_points is not None
