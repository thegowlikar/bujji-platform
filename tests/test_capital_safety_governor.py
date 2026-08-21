"""Tests — Numeric Risk Governor Gate D.1 (Capital Safety Governor).
Zero network access, zero broker/execution coupling anywhere in this
file."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bujji.trading_brain.risk_governor import capital_safety_governor as csg
from bujji.trading_brain.risk_governor.capital_safety_governor import (
    METRIC_BREACH,
    METRIC_NORMAL,
    METRIC_UNAVAILABLE,
    METRIC_WARNING,
    SAFETY_BLOCKED,
    SAFETY_CAUTION,
    SAFETY_RESTRICTED,
    SAFETY_SAFE,
    CapitalSafetySnapshot,
    CapitalSafetyThresholds,
    ProposedTradeEffect,
    classify_capital_safety,
    compute_capital_metrics,
    evaluate_trade_capital_safety,
    project_snapshot,
)


def _clock(iso="2026-08-02T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _snapshot(
    total_capital=1_000_000.0, available_capital=1_000_000.0, used_margin=200_000.0,
    open_risk=100_000.0, reserved_risk=0.0, daily_pnl=0.0, daily_loss_limit=50_000.0,
    peak_capital=1_000_000.0, max_allowed_drawdown=0.20, consecutive_losses=0,
    ts="2026-08-02T09:15:00+00:00",
):
    return CapitalSafetySnapshot(
        total_capital=total_capital, available_capital=available_capital, used_margin=used_margin,
        open_risk=open_risk, reserved_risk=reserved_risk, daily_pnl=daily_pnl,
        daily_loss_limit=daily_loss_limit, peak_capital=peak_capital,
        max_allowed_drawdown=max_allowed_drawdown, consecutive_losses=consecutive_losses,
        timestamp=datetime.fromisoformat(ts),
    )


def _effect(margin=0.0, max_loss=0.0):
    return ProposedTradeEffect(additional_margin=margin, additional_max_loss=max_loss)


# --------------------------------------------------------------------- #
# Basic
# --------------------------------------------------------------------- #

def test_healthy_account_allows_trade():
    decision = evaluate_trade_capital_safety(_snapshot(), _effect(50_000.0, 30_000.0), None, clock=_clock())
    assert decision.allowed is True
    assert decision.status == SAFETY_SAFE


def test_warning_state_allows_with_warning():
    # margin_utilization after: (200000+520000)/1000000 = 72% -> WARNING (>=0.70, <0.90)
    decision = evaluate_trade_capital_safety(_snapshot(), _effect(520_000.0, 0.0), None, clock=_clock())
    assert decision.allowed is True
    assert decision.status == SAFETY_CAUTION
    assert len(decision.warnings) > 0


def test_restricted_state_blocks_risky_additions():
    snapshot = _snapshot(used_margin=700_000.0)   # already 70% before the trade
    decision = evaluate_trade_capital_safety(snapshot, _effect(250_000.0, 0.0), None, clock=_clock())
    assert decision.allowed is False
    assert decision.status == SAFETY_RESTRICTED


def test_restricted_state_allows_derisking_trade():
    """A trade that does NOT increase margin/risk (e.g. closing or
    hedging) must still be allowed even in a RESTRICTED state."""
    snapshot = _snapshot(used_margin=950_000.0)   # already past breach on its own
    decision = evaluate_trade_capital_safety(snapshot, _effect(0.0, 0.0), None, clock=_clock())
    assert decision.status == SAFETY_RESTRICTED
    assert decision.allowed is True


# --------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------- #

def test_missing_capital_data_fails_closed():
    snapshot = _snapshot(total_capital=None)
    decision = evaluate_trade_capital_safety(snapshot, _effect(1000.0, 0.0), None, clock=_clock())
    assert decision.allowed is False
    assert decision.status == SAFETY_BLOCKED
    assert any("INSUFFICIENT_CAPITAL_DATA" in r for r in decision.blocking_reasons)


def test_negative_capital_fails_closed():
    snapshot = _snapshot(total_capital=-100.0)
    decision = evaluate_trade_capital_safety(snapshot, _effect(1000.0, 0.0), None, clock=_clock())
    assert decision.allowed is False
    assert decision.status == SAFETY_BLOCKED
    assert any("SAFETY_INVARIANT_VIOLATION" in r for r in decision.blocking_reasons)


def test_zero_total_capital_handled_correctly_never_divides_by_zero():
    snapshot = _snapshot(total_capital=0.0, used_margin=0.0, open_risk=0.0)
    decision = evaluate_trade_capital_safety(snapshot, _effect(0.0, 0.0), None, clock=_clock())
    # zero capital is not negative (no invariant violation) but produces
    # an unusable denominator -> metrics must be UNAVAILABLE -> BLOCKED
    assert decision.status == SAFETY_BLOCKED
    assert decision.allowed is False


def test_daily_loss_breach_blocks():
    # already breached BEFORE the trade -> correctly reported via the
    # before-state ACCOUNT_ALREADY_BLOCKED path (see the dedicated
    # already-blocked test below for that path's own direct coverage).
    snapshot = _snapshot(daily_pnl=-51_000.0, daily_loss_limit=50_000.0)   # 102% of limit
    decision = evaluate_trade_capital_safety(snapshot, _effect(0.0, 0.0), None, clock=_clock())
    assert decision.allowed is False
    assert decision.status == SAFETY_BLOCKED
    assert any("DAILY_LOSS_LIMIT_BREACHED" in r for r in decision.blocking_reasons)


def test_drawdown_breach_blocks():
    snapshot = _snapshot(peak_capital=1_000_000.0, total_capital=790_000.0, max_allowed_drawdown=0.20)
    decision = evaluate_trade_capital_safety(snapshot, _effect(0.0, 0.0), None, clock=_clock())
    assert decision.allowed is False
    assert decision.status == SAFETY_BLOCKED
    assert any("MAXIMUM_DRAWDOWN_BREACHED" in r for r in decision.blocking_reasons)


def test_already_blocked_account_cannot_be_overridden_by_a_safe_looking_trade():
    """Part 6: a blocked account cannot be unblocked by a hypothetical
    trade's own merits, even a purely risk-reducing one."""
    snapshot = _snapshot(daily_pnl=-60_000.0, daily_loss_limit=50_000.0)   # already blocked before any trade
    decision = evaluate_trade_capital_safety(snapshot, _effect(0.0, 0.0), None, clock=_clock())
    assert decision.allowed is False
    assert decision.status == SAFETY_BLOCKED
    assert any("ACCOUNT_ALREADY_BLOCKED" in r for r in decision.blocking_reasons)


def test_no_force_approve_parameter_exists():
    """Structural proof: evaluate_trade_capital_safety's signature has
    no override/force flag of any kind."""
    sig = inspect.signature(evaluate_trade_capital_safety)
    forbidden = {"force", "override", "force_approve", "bypass", "skip_checks"}
    assert forbidden.isdisjoint(sig.parameters.keys())


# --------------------------------------------------------------------- #
# Portfolio
# --------------------------------------------------------------------- #

def test_single_trade_acceptable_in_isolation():
    empty_book = _snapshot(used_margin=0.0, open_risk=0.0)
    decision = evaluate_trade_capital_safety(empty_book, _effect(50_000.0, 30_000.0), None, clock=_clock())
    assert decision.allowed is True


def test_whole_portfolio_makes_previously_acceptable_trade_unsafe():
    """The exact scenario from the task spec: a trade that looks fine
    alone becomes unsafe once the existing book's usage is accounted
    for."""
    heavily_used_book = _snapshot(used_margin=700_000.0)   # 70% already used
    same_trade = _effect(250_000.0, 50_000.0)   # would push to 95%
    decision = evaluate_trade_capital_safety(heavily_used_book, same_trade, None, clock=_clock())
    assert decision.allowed is False

    empty_book = _snapshot(used_margin=0.0, open_risk=0.0)
    decision_isolated = evaluate_trade_capital_safety(empty_book, same_trade, None, clock=_clock())
    assert decision_isolated.allowed is True   # same trade, different existing book -> different outcome


def test_adding_hedge_improves_safety():
    """A trade that REDUCES open_risk (a hedge, expressed as a negative
    additional_max_loss) must never be treated as riskier than doing
    nothing."""
    snapshot = _snapshot(used_margin=500_000.0, open_risk=400_000.0)
    hedge_effect = ProposedTradeEffect(additional_margin=20_000.0, additional_max_loss=-150_000.0)
    decision = evaluate_trade_capital_safety(snapshot, hedge_effect, None, clock=_clock())
    after_risk_metric = next(m for m in decision.after_metrics if m.name == "risk_utilization")
    before_risk_metric = next(m for m in decision.before_metrics if m.name == "risk_utilization")
    assert after_risk_metric.value < before_risk_metric.value


def test_removing_hedge_increases_risk():
    snapshot = _snapshot(used_margin=500_000.0, open_risk=100_000.0)   # hedge already reduced risk
    remove_hedge_effect = ProposedTradeEffect(additional_margin=-10_000.0, additional_max_loss=200_000.0)
    decision = evaluate_trade_capital_safety(snapshot, remove_hedge_effect, None, clock=_clock())
    after_risk_metric = next(m for m in decision.after_metrics if m.name == "risk_utilization")
    before_risk_metric = next(m for m in decision.before_metrics if m.name == "risk_utilization")
    assert after_risk_metric.value > before_risk_metric.value


# --------------------------------------------------------------------- #
# Integrity
# --------------------------------------------------------------------- #

def test_explanation_matches_decision_allowed():
    decision = evaluate_trade_capital_safety(_snapshot(), _effect(10_000.0, 5_000.0), None, clock=_clock())
    assert decision.allowed is True
    assert "allowed" in decision.explanation.lower()
    assert "rejected" not in decision.explanation.lower()


def test_explanation_matches_decision_blocked():
    snapshot = _snapshot(daily_pnl=-60_000.0, daily_loss_limit=50_000.0)
    decision = evaluate_trade_capital_safety(snapshot, _effect(0.0, 0.0), None, clock=_clock())
    assert decision.allowed is False
    assert "rejected" in decision.explanation.lower()
    assert "DAILY_LOSS_LIMIT_BREACHED" in decision.explanation


def test_explanation_uses_the_same_after_metrics_object_not_a_recomputation():
    """The explanation string's percentages must derive from the exact
    same after_metrics tuple returned on the decision -- verified by
    checking the formatted percentage matches the metric's own value."""
    decision = evaluate_trade_capital_safety(_snapshot(), _effect(50_000.0, 30_000.0), None, clock=_clock())
    margin_metric = next(m for m in decision.after_metrics if m.name == "margin_utilization")
    expected_pct_text = f"{margin_metric.value:.0%}"
    assert expected_pct_text in decision.explanation


def test_no_broker_imports():
    source_path = Path(inspect.getfile(csg))
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
    source_path = Path(inspect.getfile(csg))
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
    """This module must not import capital_check.py -- it is a SEPARATE,
    additional gate, never a replacement, and must never re-derive
    capital_check's own ALLOW/VETO rule."""
    source_path = Path(inspect.getfile(csg))
    tree = ast.parse(source_path.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offenders = [m for m in imported if "capital_check" in m]
    assert offenders == [], f"must not import capital_check: {offenders}"


def test_no_threshold_drift_between_before_and_after_computation():
    """The SAME CapitalSafetyThresholds instance must govern both the
    before and after metric computation within one decision -- proven
    by using a custom threshold set and confirming both metric sets
    reflect it identically."""
    custom = CapitalSafetyThresholds()
    from bujji.trading_brain.risk_governor.capital_safety_governor import MetricThresholds
    custom = CapitalSafetyThresholds(
        margin_utilization=MetricThresholds(warning=0.10, breach=0.20),
        risk_utilization=MetricThresholds(warning=0.70, breach=0.90),
        daily_loss_utilization=MetricThresholds(warning=0.70, breach=1.0),
        drawdown_utilization=MetricThresholds(warning=0.70, breach=1.0),
    )
    decision = evaluate_trade_capital_safety(_snapshot(used_margin=100_000.0), _effect(0.0, 0.0), custom, clock=_clock())
    before_margin = next(m for m in decision.before_metrics if m.name == "margin_utilization")
    after_margin = next(m for m in decision.after_metrics if m.name == "margin_utilization")
    assert before_margin.threshold == 0.20
    assert after_margin.threshold == 0.20


# --------------------------------------------------------------------- #
# compute_capital_metrics / classify_capital_safety as standalone units
# --------------------------------------------------------------------- #

def test_compute_capital_metrics_normal():
    metrics = compute_capital_metrics(_snapshot(), CapitalSafetyThresholds())
    assert all(m.status == METRIC_NORMAL for m in metrics)


def test_compute_capital_metrics_unavailable_on_missing_denominator():
    snapshot = _snapshot(total_capital=None)
    metrics = compute_capital_metrics(snapshot, CapitalSafetyThresholds())
    by_name = {m.name: m for m in metrics}
    assert by_name["margin_utilization"].status == METRIC_UNAVAILABLE
    assert by_name["margin_utilization"].value is None


def test_classify_capital_safety_safe():
    snapshot = _snapshot()
    metrics = compute_capital_metrics(snapshot, CapitalSafetyThresholds())
    status, reasons = classify_capital_safety(snapshot, metrics)
    assert status == SAFETY_SAFE
    assert reasons == ()


def test_project_snapshot_never_mutates_original():
    original = _snapshot(used_margin=200_000.0, open_risk=100_000.0)
    projected = project_snapshot(original, _effect(50_000.0, 30_000.0))
    assert original.used_margin == 200_000.0   # unchanged
    assert original.open_risk == 100_000.0
    assert projected.used_margin == 250_000.0
    assert projected.open_risk == 130_000.0
    assert projected is not original


def test_project_snapshot_preserves_unavailability():
    """An unknown starting margin plus a known delta is still unknown
    -- never silently treated as 0 + delta."""
    original = _snapshot(used_margin=None)
    projected = project_snapshot(original, _effect(50_000.0, 0.0))
    assert projected.used_margin is None
