"""Liquidity Brain — unit tests (spread math correctness, classification
edge cases, data-quality gates) + a real-data regression fixture."""
import pytest

from bujji.intelligence.liquidity_brain import LiquidityBrain
from bujji.intelligence.models import DataQuality, SpreadTightness


@pytest.fixture
def brain():
    return LiquidityBrain()


# ---------------------------------------------------------------------- #
# Spread math correctness (exact arithmetic control)
# ---------------------------------------------------------------------- #
def test_combined_spread_is_sum_of_both_legs_ask_minus_bid(brain):
    reading = brain.analyze(ce_bid=100.0, ce_ask=101.0, pe_bid=50.0, pe_ask=50.5)
    assert reading.combined_spread == pytest.approx(1.5, abs=1e-9)


def test_combined_spread_pct_uses_combined_mid(brain):
    # combined_bid=150, combined_ask=151.5, combined_mid=150.75, spread=1.5
    reading = brain.analyze(ce_bid=100.0, ce_ask=101.0, pe_bid=50.0, pe_ask=50.5)
    expected_pct = 1.5 / 150.75 * 100.0
    # Brain rounds to 4 decimals for display -- compare at that precision.
    assert reading.combined_spread_pct == pytest.approx(expected_pct, abs=1e-4)


def test_per_leg_spread_pct_uses_own_leg_mid(brain):
    reading = brain.analyze(ce_bid=100.0, ce_ask=102.0, pe_bid=50.0, pe_ask=50.0)
    # CE: spread=2, mid=101 -> 1.9802%. PE: spread=0, mid=50 -> 0%.
    assert reading.ce_spread_pct == pytest.approx(2 / 101 * 100, abs=1e-4)
    assert reading.pe_spread_pct == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------- #
# Classification
# ---------------------------------------------------------------------- #
def test_tight_spread_classifies_tight(brain):
    reading = brain.analyze(ce_bid=100.0, ce_ask=100.1, pe_bid=80.0, pe_ask=80.1)
    assert reading.tightness is SpreadTightness.TIGHT


def test_wide_spread_classifies_wide(brain):
    reading = brain.analyze(ce_bid=100.0, ce_ask=106.0, pe_bid=80.0, pe_ask=85.0)
    assert reading.tightness is SpreadTightness.WIDE


def test_moderate_spread_classifies_normal(brain):
    # combined spread_pct should land strictly between 0.5% and 2%.
    reading = brain.analyze(ce_bid=100.0, ce_ask=101.0, pe_bid=80.0, pe_ask=80.8)
    assert reading.tightness is SpreadTightness.NORMAL


# ---------------------------------------------------------------------- #
# Data-quality gates -- never guess
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize("field", ["ce_bid", "ce_ask", "pe_bid", "pe_ask"])
def test_nonpositive_quote_field_returns_unknown(brain, field):
    kwargs = {"ce_bid": 100.0, "ce_ask": 101.0, "pe_bid": 80.0, "pe_ask": 80.5}
    kwargs[field] = 0.0
    reading = brain.analyze(**kwargs)
    assert reading.tightness is SpreadTightness.UNKNOWN
    assert reading.data_quality is DataQuality.INSUFFICIENT
    assert "invalid_quote" in reading.reason


def test_crossed_market_ce_returns_unknown(brain):
    reading = brain.analyze(ce_bid=101.0, ce_ask=100.0, pe_bid=80.0, pe_ask=80.5)
    assert reading.tightness is SpreadTightness.UNKNOWN
    assert "crossed_market" in reading.reason


def test_crossed_market_pe_returns_unknown(brain):
    reading = brain.analyze(ce_bid=100.0, ce_ask=101.0, pe_bid=80.5, pe_ask=80.0)
    assert reading.tightness is SpreadTightness.UNKNOWN
    assert "crossed_market" in reading.reason


def test_unknown_reading_still_reports_raw_quotes(brain):
    """Even on a bad/crossed quote, the raw bid/ask values should still
    be visible for a human to inspect -- only the derived numbers are
    withheld."""
    reading = brain.analyze(ce_bid=101.0, ce_ask=100.0, pe_bid=80.0, pe_ask=80.5)
    assert reading.ce_bid == 101.0 and reading.ce_ask == 100.0
    assert reading.combined_spread is None
    assert reading.combined_spread_pct is None


def test_render_and_to_log_do_not_raise(brain):
    reading = brain.analyze(ce_bid=100.0, ce_ask=100.1, pe_bid=80.0, pe_ask=80.1)
    assert "LIQUIDITY BRAIN" in reading.render()
    log = reading.to_log()
    assert log["brain"] == "liquidity"


def test_render_handles_unknown_reading_without_raising(brain):
    reading = brain.analyze(ce_bid=0.0, ce_ask=100.0, pe_bid=80.0, pe_ask=80.5)
    assert "NOT AVAILABLE" in reading.render()


# ---------------------------------------------------------------------- #
# Real-data regression fixture (2026-07-20, live-captured NIFTY weekly
# ATM CE/PE bid/ask -- see docs/AUDIT_LOG.md). spread == ask - bid was
# confirmed to hold exactly on both legs in the live capture, the basis
# for trusting this data source at all.
# ---------------------------------------------------------------------- #
def test_real_quote_2026_07_20_24250_strike_classifies_tight():
    brain = LiquidityBrain()
    reading = brain.analyze(ce_bid=82.4, ce_ask=82.6, pe_bid=68.0, pe_ask=68.05)
    assert reading.data_quality is DataQuality.SUFFICIENT
    assert reading.tightness is SpreadTightness.TIGHT
    assert reading.combined_spread == pytest.approx(0.25, abs=1e-9)
    assert reading.combined_spread_pct == pytest.approx(0.1661, abs=1e-3)
