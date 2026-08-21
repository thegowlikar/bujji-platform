"""Structure Brain — unit tests (wall-selection correctness, proximity
classification, data-quality gates) + a real-data regression fixture."""
from datetime import datetime

import pytest

from bujji.core.clock import IST
from bujji.intelligence.context import IntelligenceContext
from bujji.intelligence.structure_brain import StructureBrain
from bujji.intelligence.models import DataQuality, StructureProximity

TEST_CONTEXT = IntelligenceContext(as_of_time=datetime(2026, 7, 20, 9, 20, tzinfo=IST))


@pytest.fixture
def brain():
    return StructureBrain()


# ---------------------------------------------------------------------- #
# Wall selection correctness
# ---------------------------------------------------------------------- #
def test_resistance_is_highest_ce_oi_strike_above_spot(brain):
    strikes = [
        (24050, 1000, 500),   # Below spot -- irrelevant to resistance.
        (24100, 9000, 500),   # Above spot, highest CE OI -> resistance.
        (24150, 3000, 500),   # Above spot but lower CE OI.
    ]
    reading = brain.analyze(spot=24000, strikes=strikes, context=TEST_CONTEXT)
    assert reading.resistance_strike == 24100
    assert reading.resistance_oi == 9000


def test_support_is_highest_pe_oi_strike_below_spot(brain):
    strikes = [
        (23900, 500, 3000),   # Below spot but lower PE OI.
        (23950, 500, 9000),   # Below spot, highest PE OI -> support.
        (24050, 500, 1000),   # Above spot -- irrelevant to support.
    ]
    reading = brain.analyze(spot=24000, strikes=strikes, context=TEST_CONTEXT)
    assert reading.support_strike == 23950
    assert reading.support_oi == 9000


def test_only_strikes_above_spot_considered_for_resistance(brain):
    """A strike below spot with huge CE OI must never become the
    'resistance' -- resistance is only ever a strike ABOVE spot."""
    strikes = [
        (23900, 999999, 100),  # Below spot, huge CE OI -- must be ignored for resistance.
        (24100, 100, 100),
    ]
    reading = brain.analyze(spot=24000, strikes=strikes, context=TEST_CONTEXT)
    assert reading.resistance_strike == 24100


def test_only_strikes_below_spot_considered_for_support(brain):
    strikes = [
        (24100, 100, 999999),  # Above spot, huge PE OI -- must be ignored for support.
        (23900, 100, 100),
    ]
    reading = brain.analyze(spot=24000, strikes=strikes, context=TEST_CONTEXT)
    assert reading.support_strike == 23900


def test_put_call_oi_ratio_is_total_pe_over_total_ce(brain):
    strikes = [(24100, 1000, 2000), (23900, 3000, 4000)]
    reading = brain.analyze(spot=24000, strikes=strikes, context=TEST_CONTEXT)
    assert reading.put_call_oi_ratio == pytest.approx(6000 / 4000, abs=1e-6)


# ---------------------------------------------------------------------- #
# Proximity classification
# ---------------------------------------------------------------------- #
def test_spot_right_under_resistance_wall_classifies_near_resistance(brain):
    strikes = [(24010, 5000, 100), (23900, 100, 5000)]
    reading = brain.analyze(spot=24000, strikes=strikes, context=TEST_CONTEXT)  # 0.042% to resistance.
    assert reading.proximity is StructureProximity.NEAR_RESISTANCE_WALL


def test_spot_right_above_support_wall_classifies_near_support(brain):
    strikes = [(24500, 100, 100), (23990, 100, 5000)]
    reading = brain.analyze(spot=24000, strikes=strikes, context=TEST_CONTEXT)  # 0.042% to support.
    assert reading.proximity is StructureProximity.NEAR_SUPPORT_WALL


def test_spot_far_from_both_walls_classifies_mid_range(brain):
    strikes = [(24500, 100, 100), (23500, 100, 100)]
    reading = brain.analyze(spot=24000, strikes=strikes, context=TEST_CONTEXT)
    assert reading.proximity is StructureProximity.MID_RANGE


def test_nearer_wall_wins_when_both_within_threshold(brain):
    """Spot equidistant-ish to both walls but resistance is strictly
    closer -- resistance must win."""
    strikes = [(24010, 100, 100), (23980, 100, 100)]  # 0.042% vs 0.083%.
    reading = brain.analyze(spot=24000, strikes=strikes, context=TEST_CONTEXT)
    assert reading.proximity is StructureProximity.NEAR_RESISTANCE_WALL


# ---------------------------------------------------------------------- #
# Data-quality gates -- never guess
# ---------------------------------------------------------------------- #
def test_nonpositive_spot_returns_unknown(brain):
    reading = brain.analyze(spot=0.0, strikes=[(24000, 100, 100)], context=TEST_CONTEXT)
    assert reading.proximity is StructureProximity.UNKNOWN
    assert reading.data_quality is DataQuality.INSUFFICIENT
    assert "invalid_spot" in reading.reason


def test_empty_strikes_returns_unknown(brain):
    reading = brain.analyze(spot=24000, strikes=[], context=TEST_CONTEXT)
    assert reading.proximity is StructureProximity.UNKNOWN
    assert "no_valid_strikes" in reading.reason


def test_negative_oi_strikes_are_excluded_and_can_exhaust_data(brain):
    reading = brain.analyze(spot=24000, strikes=[(24100, -5, 10), (23900, 10, -5)], context=TEST_CONTEXT)
    assert reading.proximity is StructureProximity.UNKNOWN
    assert "no_valid_strikes" in reading.reason


def test_all_strikes_on_one_side_only_still_produces_a_reading(brain):
    """Only strikes above spot available -- resistance is computable,
    support is honestly None, not fabricated."""
    reading = brain.analyze(spot=24000, strikes=[(24100, 500, 100), (24150, 900, 100)], context=TEST_CONTEXT)
    assert reading.data_quality is DataQuality.SUFFICIENT
    assert reading.resistance_strike == 24150
    assert reading.support_strike is None
    assert reading.distance_to_support_pct is None


def test_render_and_to_log_do_not_raise(brain):
    reading = brain.analyze(spot=24000, strikes=[(24100, 500, 100), (23900, 100, 500)], context=TEST_CONTEXT)
    assert "STRUCTURE BRAIN" in reading.render()
    log = reading.to_log()
    assert log["brain"] == "structure"


def test_render_handles_unknown_reading_without_raising(brain):
    reading = brain.analyze(spot=24000, strikes=[], context=TEST_CONTEXT)
    assert "NOT AVAILABLE" in reading.render()


# ---------------------------------------------------------------------- #
# Real-data regression fixture (2026-07-20, live-captured NIFTY option
# chain OI for strikes 24100-24250 -- see docs/AUDIT_LOG.md). oich == oi
# - prev_oi was confirmed to hold exactly on every strike in the live
# capture, the basis for trusting this data source at all.
# ---------------------------------------------------------------------- #
def test_real_option_chain_2026_07_20_classifies_near_resistance():
    brain = StructureBrain()
    spot = 24243.1
    strikes = [
        (24100, 3940820, 17299295),
        (24150, 4531800, 16936140),
        (24200, 12891320, 25869350),
        (24250, 12541490, 13034125),
    ]
    reading = brain.analyze(spot, strikes, context=TEST_CONTEXT)
    assert reading.data_quality is DataQuality.SUFFICIENT
    assert reading.resistance_strike == 24250
    assert reading.support_strike == 24200
    assert reading.proximity is StructureProximity.NEAR_RESISTANCE_WALL
    assert reading.distance_to_resistance_pct == pytest.approx(0.0285, abs=1e-3)
    assert reading.put_call_oi_ratio == pytest.approx(2.157, abs=1e-2)
