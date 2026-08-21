"""Event Brain — unit tests (expiry-date arithmetic correctness, VIX
regime classification, data-quality gates) + a real-data regression
fixture."""
from datetime import date, datetime

import pytest

from bujji.core.clock import IST
from bujji.intelligence.context import IntelligenceContext
from bujji.intelligence.event_brain import EventBrain
from bujji.intelligence.models import DataQuality, ExpiryProximity, VixRegime

TEST_CONTEXT = IntelligenceContext(as_of_time=datetime(2026, 7, 20, 9, 20, tzinfo=IST))


@pytest.fixture
def brain():
    return EventBrain()


EXPIRY = date(2026, 7, 21)


# ---------------------------------------------------------------------- #
# Expiry-date arithmetic correctness
# ---------------------------------------------------------------------- #
def test_same_day_as_expiry_is_expiry_day(brain):
    reading = brain.analyze(expiry_date=EXPIRY, today=EXPIRY, vix_level=15.0, context=TEST_CONTEXT)
    assert reading.days_to_expiry == 0
    assert reading.expiry_proximity is ExpiryProximity.EXPIRY_DAY


def test_one_day_before_expiry_is_expiry_eve(brain):
    reading = brain.analyze(expiry_date=EXPIRY, today=date(2026, 7, 20), vix_level=15.0, context=TEST_CONTEXT)
    assert reading.days_to_expiry == 1
    assert reading.expiry_proximity is ExpiryProximity.EXPIRY_EVE


def test_several_days_before_expiry_is_normal(brain):
    reading = brain.analyze(expiry_date=EXPIRY, today=date(2026, 7, 15), vix_level=15.0, context=TEST_CONTEXT)
    assert reading.days_to_expiry == 6
    assert reading.expiry_proximity is ExpiryProximity.NORMAL


def test_today_after_expiry_is_unknown_never_guessed(brain):
    """today > expiry_date is an inconsistent/stale input -- refuse
    rather than report a nonsensical negative days-to-expiry."""
    reading = brain.analyze(expiry_date=EXPIRY, today=date(2026, 7, 22), vix_level=15.0, context=TEST_CONTEXT)
    assert reading.expiry_proximity is ExpiryProximity.UNKNOWN
    assert reading.days_to_expiry is None


# ---------------------------------------------------------------------- #
# VIX regime classification
# ---------------------------------------------------------------------- #
def test_low_vix_classifies_low(brain):
    reading = brain.analyze(expiry_date=EXPIRY, today=EXPIRY, vix_level=11.0, context=TEST_CONTEXT)
    assert reading.vix_regime is VixRegime.LOW


def test_moderate_vix_classifies_moderate(brain):
    reading = brain.analyze(expiry_date=EXPIRY, today=EXPIRY, vix_level=16.0, context=TEST_CONTEXT)
    assert reading.vix_regime is VixRegime.MODERATE


def test_elevated_vix_classifies_elevated(brain):
    reading = brain.analyze(expiry_date=EXPIRY, today=EXPIRY, vix_level=25.0, context=TEST_CONTEXT)
    assert reading.vix_regime is VixRegime.ELEVATED


def test_vix_change_pct_computed_from_real_prev_close(brain):
    reading = brain.analyze(expiry_date=EXPIRY, today=EXPIRY, vix_level=15.0, vix_prev_close=12.0, context=TEST_CONTEXT)
    assert reading.vix_change_pct == pytest.approx(25.0, abs=1e-6)


def test_vix_change_pct_none_without_prev_close(brain):
    reading = brain.analyze(expiry_date=EXPIRY, today=EXPIRY, vix_level=15.0, context=TEST_CONTEXT)
    assert reading.vix_change_pct is None
    assert reading.vix_regime is VixRegime.MODERATE  # Level alone is still classifiable.


# ---------------------------------------------------------------------- #
# Data-quality gates and partial-data honesty
# ---------------------------------------------------------------------- #
def test_missing_vix_still_reports_expiry_proximity(brain):
    """Expiry proximity and VIX regime are independent dimensions -- a
    missing VIX must not block the (fully computable, no-external-data)
    expiry reading."""
    reading = brain.analyze(expiry_date=EXPIRY, today=EXPIRY, vix_level=None, context=TEST_CONTEXT)
    assert reading.expiry_proximity is ExpiryProximity.EXPIRY_DAY
    assert reading.vix_regime is VixRegime.UNKNOWN
    assert reading.data_quality is DataQuality.SUFFICIENT
    assert reading.confidence == 0.5


def test_invalid_dates_still_reports_vix_regime(brain):
    reading = brain.analyze(expiry_date=EXPIRY, today=date(2026, 7, 22), vix_level=15.0, context=TEST_CONTEXT)
    assert reading.expiry_proximity is ExpiryProximity.UNKNOWN
    assert reading.vix_regime is VixRegime.MODERATE
    assert reading.data_quality is DataQuality.SUFFICIENT
    assert reading.confidence == 0.5


def test_both_dimensions_missing_returns_fully_insufficient(brain):
    reading = brain.analyze(expiry_date=EXPIRY, today=date(2026, 7, 22), vix_level=None, context=TEST_CONTEXT)
    assert reading.expiry_proximity is ExpiryProximity.UNKNOWN
    assert reading.vix_regime is VixRegime.UNKNOWN
    assert reading.data_quality is DataQuality.INSUFFICIENT
    assert reading.confidence == 0.0


def test_nonpositive_vix_treated_as_missing(brain):
    reading = brain.analyze(expiry_date=EXPIRY, today=EXPIRY, vix_level=0.0, context=TEST_CONTEXT)
    assert reading.vix_regime is VixRegime.UNKNOWN


def test_render_and_to_log_do_not_raise(brain):
    reading = brain.analyze(expiry_date=EXPIRY, today=EXPIRY, vix_level=15.0, context=TEST_CONTEXT)
    assert "EVENT BRAIN" in reading.render()
    log = reading.to_log()
    assert log["brain"] == "event"


def test_render_flags_calendar_events_as_not_covered(brain):
    """The render must be honest that FOMC/RBI/Budget-style events are
    not part of this brain's scope -- not silently absent."""
    reading = brain.analyze(expiry_date=EXPIRY, today=EXPIRY, vix_level=15.0, context=TEST_CONTEXT)
    assert "NOT covered" in reading.render()


# ---------------------------------------------------------------------- #
# Real-data regression fixture (2026-07-20, live-captured India VIX --
# see docs/AUDIT_LOG.md)
# ---------------------------------------------------------------------- #
def test_real_vix_2026_07_20_and_real_expiry_20260721():
    brain = EventBrain()
    reading = brain.analyze(
        expiry_date=date(2026, 7, 21), today=date(2026, 7, 20),
        vix_level=13.02, vix_prev_close=13.15,
     context=TEST_CONTEXT)
    assert reading.data_quality is DataQuality.SUFFICIENT
    assert reading.days_to_expiry == 1
    assert reading.expiry_proximity is ExpiryProximity.EXPIRY_EVE
    assert reading.vix_regime is VixRegime.MODERATE
    assert reading.vix_change_pct == pytest.approx(-0.989, abs=1e-2)
