"""Tests — Numeric Risk Governor capital/margin check."""
from __future__ import annotations

from datetime import datetime, timezone

from bujji.trading_brain.risk_governor.capital_check import CapitalCheckInput, assess_capital


def _clock(iso="2026-08-01T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def test_unverified_margin_always_vetoes_no_matter_how_favorable_everything_else_is():
    inputs = CapitalCheckInput(
        margin_verified=False, required_margin=1.0, available_capital=1_000_000.0,
        configured_risk_capital=1_000_000.0, margin_source="NONE",
    )
    result = assess_capital(inputs, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "MARGIN_NOT_CERTIFIED"


def test_verified_margin_within_capital_allows():
    inputs = CapitalCheckInput(
        margin_verified=True, required_margin=5000.0, available_capital=10000.0,
        configured_risk_capital=8000.0, margin_source="TEST_FIXTURE",
    )
    result = assess_capital(inputs, clock=_clock())
    assert result.decision == "ALLOW"


def test_exceeds_configured_risk_capital_even_if_broker_capital_is_sufficient():
    inputs = CapitalCheckInput(
        margin_verified=True, required_margin=9000.0, available_capital=100000.0,
        configured_risk_capital=8000.0, margin_source="TEST_FIXTURE",
    )
    result = assess_capital(inputs, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "CAPITAL_EXCEEDED"


def test_exceeds_available_broker_capital_even_if_configured_ceiling_is_generous():
    inputs = CapitalCheckInput(
        margin_verified=True, required_margin=9000.0, available_capital=5000.0,
        configured_risk_capital=1_000_000.0, margin_source="TEST_FIXTURE",
    )
    result = assess_capital(inputs, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "CAPITAL_EXCEEDED"


def test_missing_required_margin_fails_closed():
    inputs = CapitalCheckInput(
        margin_verified=True, required_margin=None, available_capital=10000.0,
        configured_risk_capital=8000.0, margin_source="TEST_FIXTURE",
    )
    result = assess_capital(inputs, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "MARGIN_DATA_MISSING"


def test_missing_available_capital_fails_closed():
    inputs = CapitalCheckInput(
        margin_verified=True, required_margin=5000.0, available_capital=None,
        configured_risk_capital=8000.0, margin_source="TEST_FIXTURE",
    )
    result = assess_capital(inputs, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "CAPITAL_DATA_MISSING"


def test_negative_figures_fail_closed():
    for kwargs in [
        dict(required_margin=-1.0, available_capital=10000.0, configured_risk_capital=8000.0),
        dict(required_margin=1.0, available_capital=-10000.0, configured_risk_capital=8000.0),
        dict(required_margin=1.0, available_capital=10000.0, configured_risk_capital=-8000.0),
    ]:
        inputs = CapitalCheckInput(margin_verified=True, margin_source="TEST_FIXTURE", **kwargs)
        result = assess_capital(inputs, clock=_clock())
        assert result.decision == "VETO"


def test_exactly_at_boundary_allows():
    inputs = CapitalCheckInput(
        margin_verified=True, required_margin=8000.0, available_capital=10000.0,
        configured_risk_capital=8000.0, margin_source="TEST_FIXTURE",
    )
    result = assess_capital(inputs, clock=_clock())
    assert result.decision == "ALLOW"
