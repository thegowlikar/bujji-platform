"""Tests — Numeric Risk Governor Gate C.2 (capital utilization
intelligence). Zero network access anywhere in this file."""
from __future__ import annotations

import math

import pytest

from bujji.trading_brain.risk_governor.capital_utilization import (
    UTILIZATION_BLOCKED,
    UTILIZATION_HEALTHY,
    UTILIZATION_WARNING,
    assess_capital_utilization,
)


# --------------------------------------------------------------------- #
# Case 4 -- low usage
# --------------------------------------------------------------------- #

def test_low_usage_is_healthy():
    report = assess_capital_utilization(available_capital=1_000_000.0, required_margin=400_000.0)
    assert report.usage_fraction == pytest.approx(0.4)
    assert report.status == UTILIZATION_HEALTHY


# --------------------------------------------------------------------- #
# Case 5 -- medium usage
# --------------------------------------------------------------------- #

def test_medium_usage_is_warning():
    report = assess_capital_utilization(available_capital=1_000_000.0, required_margin=600_000.0)
    assert report.usage_fraction == pytest.approx(0.6)
    assert report.status == UTILIZATION_WARNING


def test_exactly_at_healthy_boundary_is_warning_not_healthy():
    """50% is the boundary -- the spec's ">=50%" language means exactly
    50% falls into WARNING, not HEALTHY."""
    report = assess_capital_utilization(available_capital=1_000_000.0, required_margin=500_000.0)
    assert report.status == UTILIZATION_WARNING


def test_exactly_at_warning_boundary_is_still_warning_not_blocked():
    """75% is the boundary -- the spec's "50-75%" range is inclusive of
    75%, matching ">75%" for BLOCKED."""
    report = assess_capital_utilization(available_capital=1_000_000.0, required_margin=750_000.0)
    assert report.status == UTILIZATION_WARNING


# --------------------------------------------------------------------- #
# Case 6 -- high usage
# --------------------------------------------------------------------- #

def test_high_usage_is_blocked():
    report = assess_capital_utilization(available_capital=1_000_000.0, required_margin=820_000.0)
    assert report.usage_fraction == pytest.approx(0.82)
    assert report.status == UTILIZATION_BLOCKED


# --------------------------------------------------------------------- #
# Configurable thresholds
# --------------------------------------------------------------------- #

def test_thresholds_are_configurable():
    # 30% usage would be HEALTHY under the default 50%/75% thresholds,
    # but WARNING under a tighter 20%/40% policy -- proving the
    # thresholds are actually read, not hardcoded.
    default_report = assess_capital_utilization(available_capital=1_000_000.0, required_margin=300_000.0)
    tight_report = assess_capital_utilization(
        available_capital=1_000_000.0, required_margin=300_000.0,
        healthy_threshold=0.2, warning_threshold=0.4,
    )
    assert default_report.status == UTILIZATION_HEALTHY
    assert tight_report.status == UTILIZATION_WARNING


# --------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------- #

def test_zero_available_capital_with_zero_margin_is_healthy_not_blocked():
    """Nothing needed, nothing available -- genuinely nothing at risk,
    must not be misclassified as BLOCKED."""
    report = assess_capital_utilization(available_capital=0.0, required_margin=0.0)
    assert report.status == UTILIZATION_HEALTHY
    assert report.usage_fraction == 0.0


def test_zero_available_capital_with_positive_margin_is_blocked():
    report = assess_capital_utilization(available_capital=0.0, required_margin=100.0)
    assert report.status == UTILIZATION_BLOCKED
    assert math.isinf(report.usage_fraction)


def test_negative_available_capital_is_blocked():
    report = assess_capital_utilization(available_capital=-500.0, required_margin=100.0)
    assert report.status == UTILIZATION_BLOCKED


def test_negative_required_margin_raises_never_silently_computed():
    with pytest.raises(ValueError):
        assess_capital_utilization(available_capital=1000.0, required_margin=-1.0)


def test_none_available_capital_raises():
    with pytest.raises(ValueError):
        assess_capital_utilization(available_capital=None, required_margin=100.0)


def test_none_required_margin_raises():
    with pytest.raises(ValueError):
        assess_capital_utilization(available_capital=1000.0, required_margin=None)


def test_inverted_thresholds_raise():
    with pytest.raises(ValueError):
        assess_capital_utilization(
            available_capital=1000.0, required_margin=100.0,
            healthy_threshold=0.8, warning_threshold=0.5,
        )
