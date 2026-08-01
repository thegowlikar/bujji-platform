"""Tests — Numeric Risk Governor Gate D.3 (Risk Budget Allocation &
Position Sizing Governor). Zero network access, zero broker/execution
coupling anywhere in this file."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bujji.trading_brain.risk_governor import risk_budget_governor as rbg
from bujji.trading_brain.risk_governor.capital_safety_governor import (
    SAFETY_BLOCKED,
    SAFETY_CAUTION,
    SAFETY_RESTRICTED,
    SAFETY_SAFE,
    CapitalSafetySnapshot,
)
from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import PortfolioRiskSnapshot
from bujji.trading_brain.risk_governor.risk_budget_governor import (
    BUDGET_APPROVED,
    BUDGET_APPROVED_WITH_WARNING,
    BUDGET_REDUCED_SIZE_REQUIRED,
    BUDGET_REJECTED,
    IllegalRiskBudgetInputError,
    RiskPolicy,
    assess_trade_risk_budget,
    calculate_available_risk_budget,
    calculate_safe_position_size,
    classify_strategy_risk,
)


def _clock(iso="2026-08-02T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _capital_snapshot(total_capital=1_000_000.0, used_margin=200_000.0, **overrides):
    defaults = dict(
        total_capital=total_capital, available_capital=1_000_000.0, used_margin=used_margin,
        open_risk=32_000.0, reserved_risk=0.0, daily_pnl=0.0, daily_loss_limit=50_000.0,
        peak_capital=1_000_000.0, max_allowed_drawdown=0.20, consecutive_losses=0, timestamp=_clock()(),
    )
    defaults.update(overrides)
    return CapitalSafetySnapshot(**defaults)


def _portfolio_snapshot(total_max_loss=32_000.0, **overrides):
    defaults = dict(
        number_of_positions=1, active_position_groups=("PG-1",), total_quantity=75, timestamp=_clock()(),
        total_margin_required=200_000.0, total_max_loss=total_max_loss, total_notional_exposure=None,
        total_short_exposure=None, total_long_exposure=None, largest_position_concentration=1.0,
        strategy_concentration={"S1": 1.0},
    )
    defaults.update(overrides)
    return PortfolioRiskSnapshot(**defaults)


# --------------------------------------------------------------------- #
# Budget calculation
# --------------------------------------------------------------------- #

def test_empty_portfolio_full_budget_available():
    cap = _capital_snapshot()
    port = _portfolio_snapshot(total_max_loss=0.0)
    budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_SAFE, clock=_clock())
    assert budget.maximum_risk_budget == 50_000.0
    assert budget.available_risk_budget == 50_000.0
    assert budget.remaining_risk_capacity == 50_000.0


def test_healthy_account_matches_worked_example():
    cap = _capital_snapshot()
    port = _portfolio_snapshot(total_max_loss=32_000.0)
    budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_SAFE, clock=_clock())
    assert budget.maximum_risk_budget == 50_000.0
    assert budget.available_risk_budget == 18_000.0
    assert budget.remaining_risk_capacity == 18_000.0


def test_high_utilization_account_can_go_over_budget():
    cap = _capital_snapshot()
    port = _portfolio_snapshot(total_max_loss=60_000.0)   # already over the 50000 max
    budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_SAFE, clock=_clock())
    assert budget.available_risk_budget == -10_000.0   # honest negative figure, not clamped
    assert budget.remaining_risk_capacity == 0.0         # but remaining capacity never negative


def test_blocked_account_has_zero_remaining_capacity_even_with_headroom():
    cap = _capital_snapshot()
    port = _portfolio_snapshot(total_max_loss=1_000.0)   # tiny usage, huge raw headroom
    budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_BLOCKED, clock=_clock())
    assert budget.available_risk_budget == 49_000.0   # raw arithmetic still computed, for transparency
    assert budget.remaining_risk_capacity == 0.0        # but usable capacity is forced to zero


def test_restricted_account_also_has_zero_remaining_capacity():
    cap = _capital_snapshot()
    port = _portfolio_snapshot(total_max_loss=1_000.0)
    budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_RESTRICTED, clock=_clock())
    assert budget.remaining_risk_capacity == 0.0


def test_insufficient_data_leaves_fields_explicitly_none():
    cap = _capital_snapshot(total_capital=None)
    port = _portfolio_snapshot(total_max_loss=None)
    budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_SAFE, clock=_clock())
    assert budget.maximum_risk_budget is None
    assert budget.available_risk_budget is None
    assert budget.remaining_risk_capacity is None


# --------------------------------------------------------------------- #
# Position sizing
# --------------------------------------------------------------------- #

def test_full_size_allowed_when_within_budget():
    cap = _capital_snapshot()
    port = _portfolio_snapshot(total_max_loss=32_000.0)
    budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_SAFE, clock=_clock())
    rec = calculate_safe_position_size(desired_quantity=100, requested_risk=10_000.0, risk_budget_snapshot=budget)
    assert rec.recommended_quantity == 100
    assert rec.reduction_required is False


def test_partial_size_recommended_matches_worked_example():
    cap = _capital_snapshot()
    port = _portfolio_snapshot(total_max_loss=32_000.0)   # remaining = 18000
    budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_SAFE, clock=_clock())
    rec = calculate_safe_position_size(desired_quantity=100, requested_risk=30_000.0, risk_budget_snapshot=budget)
    assert rec.recommended_quantity == 60   # 18000/30000 = 60%
    assert rec.reduction_required is True
    assert rec.maximum_quantity == 100


def test_zero_size_rejected_on_zero_budget():
    cap = _capital_snapshot()
    port = _portfolio_snapshot(total_max_loss=60_000.0)   # over budget -> remaining capacity 0
    budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_SAFE, clock=_clock())
    rec = calculate_safe_position_size(desired_quantity=100, requested_risk=5_000.0, risk_budget_snapshot=budget)
    assert rec.recommended_quantity == 0
    assert rec.reduction_required is True


def test_recommended_quantity_never_exceeds_maximum():
    cap = _capital_snapshot()
    port = _portfolio_snapshot(total_max_loss=0.0)
    budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_SAFE, clock=_clock())
    for requested in (1.0, 100.0, 10_000.0, 1_000_000.0):
        rec = calculate_safe_position_size(desired_quantity=50, requested_risk=requested, risk_budget_snapshot=budget)
        assert rec.recommended_quantity <= rec.maximum_quantity


def test_negative_requested_risk_raises():
    cap = _capital_snapshot()
    port = _portfolio_snapshot()
    budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_SAFE, clock=_clock())
    with pytest.raises(IllegalRiskBudgetInputError):
        calculate_safe_position_size(desired_quantity=50, requested_risk=-1000.0, risk_budget_snapshot=budget)


# --------------------------------------------------------------------- #
# Portfolio awareness
# --------------------------------------------------------------------- #

def test_same_trade_allowed_in_low_risk_portfolio():
    cap = _capital_snapshot()
    port = _portfolio_snapshot(total_max_loss=5_000.0)   # low existing usage
    budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_SAFE, clock=_clock())
    decision = assess_trade_risk_budget(port, 10_000.0, budget, RiskPolicy(), clock=_clock())
    assert decision.status in (BUDGET_APPROVED, BUDGET_APPROVED_WITH_WARNING)
    assert decision.allowed is True


def test_same_trade_reduced_in_high_risk_portfolio():
    cap = _capital_snapshot()
    port_light = _portfolio_snapshot(total_max_loss=5_000.0)
    port_heavy = _portfolio_snapshot(total_max_loss=45_000.0)   # near budget cap already
    budget_light = calculate_available_risk_budget(cap, port_light, RiskPolicy(), SAFETY_SAFE, clock=_clock())
    budget_heavy = calculate_available_risk_budget(cap, port_heavy, RiskPolicy(), SAFETY_SAFE, clock=_clock())

    decision_light = assess_trade_risk_budget(port_light, 10_000.0, budget_light, RiskPolicy(), clock=_clock())
    decision_heavy = assess_trade_risk_budget(port_heavy, 10_000.0, budget_heavy, RiskPolicy(), clock=_clock())
    assert decision_light.allowed is True
    assert decision_heavy.status == BUDGET_REDUCED_SIZE_REQUIRED
    assert decision_heavy.allowed is False


def test_hedge_increases_available_capacity():
    cap = _capital_snapshot()
    port = _portfolio_snapshot(total_max_loss=45_000.0)
    budget_before = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_SAFE, clock=_clock())
    hedge_decision = assess_trade_risk_budget(port, -20_000.0, budget_before, RiskPolicy(), clock=_clock())
    assert hedge_decision.allowed is True
    assert hedge_decision.remaining_after_trade > budget_before.remaining_risk_capacity


# --------------------------------------------------------------------- #
# Strategy risk allocation
# --------------------------------------------------------------------- #

def test_strategy_risk_classification_defined_vs_undefined():
    defined = classify_strategy_risk("IRON_CONDOR", 50_000.0)
    undefined = classify_strategy_risk("SHORT_DIRECTIONAL", 50_000.0)
    unknown = classify_strategy_risk("SOME_NEW_STRATEGY", 50_000.0)
    assert defined.risk_category == "DEFINED_RISK"
    assert undefined.risk_category == "UNDEFINED_RISK"
    assert unknown.risk_category == "UNKNOWN"
    assert defined.risk_amount == 50_000.0   # never a prediction, the real figure passed through


# --------------------------------------------------------------------- #
# Integrity
# --------------------------------------------------------------------- #

def test_explanation_matches_decision_approved():
    cap = _capital_snapshot()
    port = _portfolio_snapshot(total_max_loss=5_000.0)
    budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_SAFE, clock=_clock())
    decision = assess_trade_risk_budget(port, 10_000.0, budget, RiskPolicy(), clock=_clock())
    assert str(decision.requested_risk) in decision.explanation or "10000.00" in decision.explanation


def test_explanation_matches_decision_reduced():
    cap = _capital_snapshot()
    port = _portfolio_snapshot(total_max_loss=32_000.0)
    budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_SAFE, clock=_clock())
    decision = assess_trade_risk_budget(port, 30_000.0, budget, RiskPolicy(), clock=_clock())
    assert decision.status == BUDGET_REDUCED_SIZE_REQUIRED
    assert "60%" in decision.explanation


def test_explanation_matches_decision_rejected():
    cap = _capital_snapshot()
    port = _portfolio_snapshot(total_max_loss=1_000.0)
    budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_BLOCKED, clock=_clock())
    decision = assess_trade_risk_budget(port, 5_000.0, budget, RiskPolicy(), clock=_clock())
    assert decision.status == BUDGET_REJECTED
    assert "BLOCKED" in decision.explanation


def test_no_broker_imports():
    source_path = Path(inspect.getfile(rbg))
    tree = ast.parse(source_path.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offenders = [m for m in imported if m.startswith("bujji.broker")]
    assert offenders == [], f"forbidden broker imports found: {offenders}"


def test_no_execution_imports():
    source_path = Path(inspect.getfile(rbg))
    tree = ast.parse(source_path.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden_prefixes = ("bujji.runtime_execution", "bujji.production_runtime")
    offenders = [m for m in imported if any(m == p or m.startswith(p + ".") for p in forbidden_prefixes)]
    assert offenders == [], f"forbidden execution imports found: {offenders}"


def test_no_capital_check_duplication():
    source_path = Path(inspect.getfile(rbg))
    tree = ast.parse(source_path.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offenders = [m for m in imported if "capital_check" in m]
    assert offenders == [], f"must not import capital_check: {offenders}"


def test_no_d1_d2_decision_logic_duplication():
    """One-way dependency, mirroring D.2's own established discipline:
    this module imports the D.1/D.2 data models but never their own
    classification/admission functions."""
    source_path = Path(inspect.getfile(rbg))
    tree = ast.parse(source_path.read_text())
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
    assert "evaluate_trade_capital_safety" not in imported_names
    assert "classify_capital_safety" not in imported_names
    assert "aggregate_portfolio_risk" not in imported_names
    assert "classify_portfolio_risk" not in imported_names


def test_no_position_sizing_engine_duplication():
    """Confirms this module never imports the earlier-generation,
    fixed-lookup-table sizer (bujji/trading_brain/position_sizing) --
    genuinely separate mechanisms, never conflated."""
    source_path = Path(inspect.getfile(rbg))
    tree = ast.parse(source_path.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offenders = [m for m in imported if "trading_brain.position_sizing" in m]
    assert offenders == [], f"must not import the earlier-generation sizer: {offenders}"


def test_no_threshold_drift():
    cap = _capital_snapshot()
    port = _portfolio_snapshot(total_max_loss=40_000.0)
    tight_policy = RiskPolicy(max_portfolio_risk_fraction=0.02)   # 2% instead of default 5%
    default_budget = calculate_available_risk_budget(cap, port, RiskPolicy(), SAFETY_SAFE, clock=_clock())
    tight_budget = calculate_available_risk_budget(cap, port, tight_policy, SAFETY_SAFE, clock=_clock())
    assert default_budget.maximum_risk_budget == 50_000.0
    assert tight_budget.maximum_risk_budget == 20_000.0
    assert default_budget.remaining_risk_capacity != tight_budget.remaining_risk_capacity


def test_no_override_parameter_anywhere():
    forbidden = {"force", "override", "force_approve", "bypass", "skip_checks"}
    for fn in (calculate_available_risk_budget, assess_trade_risk_budget, calculate_safe_position_size):
        sig = inspect.signature(fn)
        assert forbidden.isdisjoint(sig.parameters.keys()), f"{fn.__name__} has a forbidden override parameter"
