"""Tests — Numeric Risk Governor assess() composition of Gate A + Gate B
+ portfolio limits + capital check into one RiskVerdict."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bujji.trading_brain.risk_governor.capital_check import CapitalCheckAssessment
from bujji.trading_brain.risk_governor.defined_risk import DefinedRiskAssessment
from bujji.trading_brain.risk_governor.engine import MismatchedAssessmentError, RiskVerdict, assess
from bujji.trading_brain.risk_governor.portfolio_limits import PortfolioLimitAssessment
from bujji.trading_brain.risk_governor.position_group_fold import (
    LIFECYCLE_ABORTED,
    LIFECYCLE_CONSTRUCTED,
    LIFECYCLE_OPEN,
    LIFECYCLE_UNRESOLVED,
    PositionGroupState,
)


def _clock(iso="2026-08-01T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _group_state(lifecycle_state):
    return PositionGroupState(position_group_id="PG-1", lifecycle_state=lifecycle_state)


def _defined_risk(decision, reason=None):
    return DefinedRiskAssessment(
        position_group_id="PG-1", decision=decision, blocking_reason=reason,
        max_loss=1000.0 if decision == "ALLOW" else None,
        formula_used="X" if decision == "ALLOW" else None, evaluated_at=_clock()(),
    )


def _portfolio(decision, reason=None):
    return PortfolioLimitAssessment(
        decision=decision, blocking_reason=reason, active_position_count=1,
        concentration_by_underlying={}, evaluated_at=_clock()(),
    )


def _capital(decision, reason=None):
    return CapitalCheckAssessment(decision=decision, blocking_reason=reason, evaluated_at=_clock()())


def test_all_pass_allows():
    verdict = assess(
        _group_state(LIFECYCLE_OPEN), _defined_risk("ALLOW"), _portfolio("ALLOW"), _capital("ALLOW"),
        clock=_clock(),
    )
    assert verdict.decision == "ALLOW"
    assert verdict.blocking_reason == ""
    assert verdict.failed_checks == ()
    assert len(verdict.passed_checks) == 4


def test_defined_risk_veto_produces_overall_veto():
    verdict = assess(
        _group_state(LIFECYCLE_OPEN), _defined_risk("VETO", "UNDEFINED_RISK_NO_STRESS_MODEL"),
        _portfolio("ALLOW"), _capital("ALLOW"), clock=_clock(),
    )
    assert verdict.decision == "VETO"
    assert "DEFINED_RISK_UNDEFINED_RISK_NO_STRESS_MODEL" in verdict.failed_checks


def test_portfolio_veto_produces_overall_veto():
    verdict = assess(
        _group_state(LIFECYCLE_OPEN), _defined_risk("ALLOW"),
        _portfolio("VETO", "MAX_SIMULTANEOUS_POSITIONS_EXCEEDED"), _capital("ALLOW"), clock=_clock(),
    )
    assert verdict.decision == "VETO"
    assert "PORTFOLIO_LIMITS_MAX_SIMULTANEOUS_POSITIONS_EXCEEDED" in verdict.failed_checks


def test_capital_veto_produces_overall_veto():
    verdict = assess(
        _group_state(LIFECYCLE_OPEN), _defined_risk("ALLOW"), _portfolio("ALLOW"),
        _capital("VETO", "MARGIN_NOT_CERTIFIED"), clock=_clock(),
    )
    assert verdict.decision == "VETO"
    assert "CAPITAL_MARGIN_NOT_CERTIFIED" in verdict.failed_checks


def test_lifecycle_ineligible_produces_overall_veto():
    verdict = assess(
        _group_state(LIFECYCLE_UNRESOLVED), _defined_risk("ALLOW"), _portfolio("ALLOW"), _capital("ALLOW"),
        clock=_clock(),
    )
    assert verdict.decision == "VETO"
    assert any("LIFECYCLE_NOT_ELIGIBLE" in c for c in verdict.failed_checks)


def test_terminal_lifecycle_never_allows():
    verdict = assess(
        _group_state(LIFECYCLE_ABORTED), _defined_risk("ALLOW"), _portfolio("ALLOW"), _capital("ALLOW"),
        clock=_clock(),
    )
    assert verdict.decision == "VETO"


def test_all_checks_always_evaluated_never_short_circuited():
    """Every failure mode failing simultaneously must all be recorded,
    not just the first one -- proves the four checks never short-
    circuit each other."""
    verdict = assess(
        _group_state(LIFECYCLE_UNRESOLVED),
        _defined_risk("VETO", "NO_ADVERSE_FILL_BOUND_FOR_MARKET_ORDER"),
        _portfolio("VETO", "CONCENTRATION_EXCEEDED:NIFTY"),
        _capital("VETO", "MARGIN_NOT_CERTIFIED"),
        clock=_clock(),
    )
    assert verdict.decision == "VETO"
    assert len(verdict.failed_checks) == 4
    assert verdict.passed_checks == ()


def test_blocking_reason_is_first_in_fixed_priority_order():
    verdict = assess(
        _group_state(LIFECYCLE_UNRESOLVED), _defined_risk("VETO", "X"), _portfolio("VETO", "Y"),
        _capital("VETO", "Z"), clock=_clock(),
    )
    # Fixed priority: lifecycle -> defined_risk -> portfolio_limits -> capital.
    assert verdict.blocking_reason.startswith("LIFECYCLE_NOT_ELIGIBLE")


def test_capital_is_last_in_fixed_priority_order_when_alone():
    verdict = assess(
        _group_state(LIFECYCLE_OPEN), _defined_risk("ALLOW"), _portfolio("ALLOW"),
        _capital("VETO", "MARGIN_NOT_CERTIFIED"), clock=_clock(),
    )
    assert verdict.blocking_reason == "CAPITAL_MARGIN_NOT_CERTIFIED"


def test_pre_trade_constructed_group_is_lifecycle_eligible():
    """Pre-trade entry authorization runs BEFORE anything is submitted --
    a freshly-CONSTRUCTED group must be eligible for assessment, not
    rejected as if it were some unresolved/mid-flight state."""
    verdict = assess(
        _group_state(LIFECYCLE_CONSTRUCTED), _defined_risk("ALLOW"), _portfolio("ALLOW"), _capital("ALLOW"),
        clock=_clock(),
    )
    assert verdict.decision == "ALLOW"
    assert "LIFECYCLE_ELIGIBLE" in verdict.passed_checks


def test_mismatched_position_group_ids_raise_never_silently_authorize():
    """Audit finding: assess() previously had no cross-check that
    defined_risk actually belongs to the group being assessed -- a
    caller bug (e.g. mixing up two groups' assessments) could silently
    produce ALLOW for the wrong group. Must now fail loud."""
    group_a = _group_state(LIFECYCLE_OPEN)  # position_group_id="PG-1" per the fixture helper
    defined_risk_for_different_group = DefinedRiskAssessment(
        position_group_id="PG-DIFFERENT", decision="ALLOW", blocking_reason=None,
        max_loss=1.0, formula_used="X", evaluated_at=_clock()(),
    )
    with pytest.raises(MismatchedAssessmentError):
        assess(group_a, defined_risk_for_different_group, _portfolio("ALLOW"), _capital("ALLOW"), clock=_clock())
