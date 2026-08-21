"""Tests — Numeric Risk Governor Gate D.2 (Portfolio Risk Aggregation
Engine). Zero network access, zero broker/execution coupling anywhere
in this file."""
from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bujji.trading_brain.risk_governor import portfolio_risk_aggregator as pra
from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot
from bujji.trading_brain.risk_governor.position_group_fold import LegFillState, LegState, PositionGroupState
from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import (
    RISK_CONCENTRATED,
    RISK_DIVERSIFIED,
    RISK_HEALTHY,
    RISK_HIGH_RISK,
    RISK_INVALID,
    IllegalPortfolioRiskAggregationError,
    PortfolioRiskThresholds,
    aggregate_portfolio_risk,
    aggregate_reserved_risk,
    classify_portfolio_risk,
    compute_direction_exposure,
    compute_hedge_effectiveness,
    explain_portfolio_risk,
    portfolio_risk_to_capital_safety_input,
    simulate_portfolio_change,
)
from bujji.trading_brain.risk_governor.simulated_margin_provider import MarginExplanation
from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginSnapshot


def _clock(iso="2026-08-02T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _group(pg_id, strategy_id, lifecycle="OPEN", qty=75, coid=None):
    coid = coid or f"{pg_id}-L1"
    return PositionGroupState(
        position_group_id=pg_id, lifecycle_state=lifecycle, strategy_id=strategy_id,
        legs={coid: LegState(client_order_id=coid, contract_id="C", requested_quantity=qty if lifecycle == "CONSTRUCTED" else None,
                              submit_status="LEG_NOT_SUBMITTED" if lifecycle == "CONSTRUCTED" else "LEG_ACKED",
                              fill=LegFillState(client_order_id=coid, cumulative_filled_quantity=0 if lifecycle == "CONSTRUCTED" else qty))},
    )


def _margin_snapshot(required_margin=500_000.0, verified=True):
    return MarginSnapshot(required_margin=required_margin, margin_verified=verified,
                           margin_source="SIM", as_of=_clock()(), quote=None)


def _explanation(long_exp=100_000.0, short_exp=400_000.0):
    return MarginExplanation(
        total_required_margin=500_000.0, total_long_exposure=long_exp, total_short_exposure=short_exp,
        covered_exposure=0.0, naked_exposure=short_exp, contributing_legs=(),
        highest_margin_contributor=None, risk_flags=(), risk_classification="HIGH_NAKED_EXPOSURE",
        explanation_source="SIM",
    )


# --------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------- #

def test_empty_portfolio():
    snapshot = aggregate_portfolio_risk([], None, None, None, clock=_clock())
    assert snapshot.number_of_positions == 0
    assert snapshot.total_quantity == 0
    assert snapshot.active_position_groups == ()


def test_one_position():
    groups = [_group("PG-1", "STRADDLE", qty=75)]
    snapshot = aggregate_portfolio_risk(groups, _margin_snapshot(), _explanation(), {"PG-1": 100_000.0}, clock=_clock())
    assert snapshot.number_of_positions == 1
    assert snapshot.total_quantity == 75
    assert snapshot.total_max_loss == 100_000.0
    assert snapshot.largest_position_concentration == 1.0


def test_multiple_strategies():
    groups = [_group("PG-1", "STRADDLE"), _group("PG-2", "SPREAD"), _group("PG-3", "SPREAD", coid="PG-3-L1")]
    risk_map = {"PG-1": 100_000.0, "PG-2": 50_000.0, "PG-3": 50_000.0}
    snapshot = aggregate_portfolio_risk(groups, None, None, risk_map, clock=_clock())
    assert snapshot.strategy_concentration["STRADDLE"] == pytest.approx(0.5)
    assert snapshot.strategy_concentration["SPREAD"] == pytest.approx(0.5)


def test_multiple_symbols_via_margin_explanation():
    exp = _explanation(long_exp=200_000.0, short_exp=300_000.0)
    groups = [_group("PG-1", "S1")]
    snapshot = aggregate_portfolio_risk(groups, _margin_snapshot(), exp, {"PG-1": 10_000.0}, clock=_clock())
    assert snapshot.total_long_exposure == 200_000.0
    assert snapshot.total_short_exposure == 300_000.0
    assert snapshot.total_notional_exposure == 500_000.0


def test_closed_positions_ignored():
    groups = [_group("PG-1", "STRADDLE", qty=75), _group("PG-2", "OLD", lifecycle="CLOSED", qty=9999)]
    snapshot = aggregate_portfolio_risk(groups, None, None, {"PG-1": 100_000.0}, clock=_clock())
    assert snapshot.number_of_positions == 1
    assert snapshot.total_quantity == 75
    assert "PG-2" not in snapshot.active_position_groups


def test_partial_open_included():
    groups = [_group("PG-1", "STRADDLE", lifecycle="PARTIALLY_OPEN", qty=40)]
    snapshot = aggregate_portfolio_risk(groups, None, None, {"PG-1": 60_000.0}, clock=_clock())
    assert snapshot.number_of_positions == 1
    assert snapshot.total_quantity == 40


# --------------------------------------------------------------------- #
# Risk
# --------------------------------------------------------------------- #

def test_concentrated_portfolio_detected():
    groups = [_group("PG-1", "S1"), _group("PG-2", "S2", coid="PG-2-L1")]
    risk_map = {"PG-1": 400_000.0, "PG-2": 100_000.0}   # 80% concentration
    snapshot = aggregate_portfolio_risk(groups, None, None, risk_map, clock=_clock())
    status, _ = classify_portfolio_risk(snapshot)
    assert status == RISK_HIGH_RISK


def test_diversified_portfolio_detected():
    groups = [_group(f"PG-{i}", f"S{i}", coid=f"PG-{i}-L1") for i in range(5)]
    risk_map = {f"PG-{i}": 20_000.0 for i in range(5)}   # 20% each, 5 distinct strategies
    snapshot = aggregate_portfolio_risk(groups, None, None, risk_map, clock=_clock())
    status, _ = classify_portfolio_risk(snapshot)
    assert status == RISK_DIVERSIFIED


def test_hedge_reduces_risk():
    before = pra.PortfolioRiskSnapshot(
        number_of_positions=1, active_position_groups=("PG-1",), total_quantity=75, timestamp=_clock()(),
        total_margin_required=400_000.0, total_max_loss=400_000.0, total_notional_exposure=None,
        total_short_exposure=None, total_long_exposure=None, largest_position_concentration=1.0,
        strategy_concentration={"S1": 1.0},
    )
    after = pra.PortfolioRiskSnapshot(
        number_of_positions=2, active_position_groups=("PG-1", "PG-2"), total_quantity=125, timestamp=_clock()(),
        total_margin_required=420_000.0, total_max_loss=150_000.0, total_notional_exposure=None,
        total_short_exposure=None, total_long_exposure=None, largest_position_concentration=0.67,
        strategy_concentration={"S1": 0.67, "HEDGE": 0.33},
    )
    reduction = compute_hedge_effectiveness(before, after)
    assert reduction == pytest.approx((400_000.0 - 150_000.0) / 400_000.0)
    assert reduction > 0


def test_removing_hedge_increases_risk():
    before = pra.PortfolioRiskSnapshot(
        number_of_positions=2, active_position_groups=("PG-1", "PG-2"), total_quantity=125, timestamp=_clock()(),
        total_margin_required=420_000.0, total_max_loss=150_000.0, total_notional_exposure=None,
        total_short_exposure=None, total_long_exposure=None, largest_position_concentration=0.67,
        strategy_concentration={"S1": 0.67, "HEDGE": 0.33},
    )
    after = pra.PortfolioRiskSnapshot(
        number_of_positions=1, active_position_groups=("PG-1",), total_quantity=75, timestamp=_clock()(),
        total_margin_required=400_000.0, total_max_loss=400_000.0, total_notional_exposure=None,
        total_short_exposure=None, total_long_exposure=None, largest_position_concentration=1.0,
        strategy_concentration={"S1": 1.0},
    )
    reduction = compute_hedge_effectiveness(before, after)
    assert reduction < 0   # negative "reduction" = risk increased


# --------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------- #

def test_missing_metadata_fails_closed():
    groups = [PositionGroupState(position_group_id="PG-1", lifecycle_state="OPEN", strategy_id=None,
                                  legs={"L1": LegState(client_order_id="L1", contract_id="C", requested_quantity=None,
                                                        submit_status="LEG_ACKED",
                                                        fill=LegFillState(client_order_id="L1", cumulative_filled_quantity=75))})]
    with pytest.raises(IllegalPortfolioRiskAggregationError):
        aggregate_portfolio_risk(groups, None, None, None, clock=_clock())


def test_malformed_active_leg_rejected():
    groups = [PositionGroupState(position_group_id="PG-1", lifecycle_state="OPEN", strategy_id="S1", legs={})]
    with pytest.raises(IllegalPortfolioRiskAggregationError):
        aggregate_portfolio_risk(groups, None, None, None, clock=_clock())


def test_negative_quantity_rejected():
    groups = [PositionGroupState(
        position_group_id="PG-1", lifecycle_state="OPEN", strategy_id="S1",
        legs={"L1": LegState(client_order_id="L1", contract_id="C", requested_quantity=None,
                              submit_status="LEG_ACKED",
                              fill=LegFillState(client_order_id="L1", cumulative_filled_quantity=10, reduced_quantity=50))},
    )]
    with pytest.raises(IllegalPortfolioRiskAggregationError):
        aggregate_portfolio_risk(groups, None, None, None, clock=_clock())


def test_impossible_state_missing_risk_entry_rejected():
    groups = [_group("PG-1", "S1")]
    with pytest.raises(IllegalPortfolioRiskAggregationError):
        aggregate_portfolio_risk(groups, None, None, {}, clock=_clock())   # PG-1 missing from risk map


def test_negative_risk_figure_rejected():
    groups = [_group("PG-1", "S1")]
    with pytest.raises(IllegalPortfolioRiskAggregationError):
        aggregate_portfolio_risk(groups, None, None, {"PG-1": -100.0}, clock=_clock())


# --------------------------------------------------------------------- #
# Integration
# --------------------------------------------------------------------- #

def test_d1_receives_aggregated_values():
    groups = [_group("PG-1", "S1")]
    snapshot = aggregate_portfolio_risk(groups, _margin_snapshot(500_000.0), _explanation(), {"PG-1": 300_000.0}, clock=_clock())
    d1_input = portfolio_risk_to_capital_safety_input(
        snapshot, reserved_risk=0.0, total_capital=1_000_000.0, available_capital=1_000_000.0,
        daily_pnl=0.0, daily_loss_limit=50_000.0, peak_capital=1_000_000.0, max_allowed_drawdown=0.20,
        consecutive_losses=0, clock=_clock(),
    )
    assert isinstance(d1_input, CapitalSafetySnapshot)
    assert d1_input.used_margin == 500_000.0
    assert d1_input.open_risk == 300_000.0


def test_portfolio_level_risk_affects_trade_admission():
    from bujji.trading_brain.risk_governor.capital_safety_governor import (
        ProposedTradeEffect, evaluate_trade_capital_safety,
    )
    groups = [_group("PG-1", "S1")]
    snapshot = aggregate_portfolio_risk(groups, _margin_snapshot(700_000.0), _explanation(), {"PG-1": 300_000.0}, clock=_clock())
    d1_input = portfolio_risk_to_capital_safety_input(
        snapshot, reserved_risk=0.0, total_capital=1_000_000.0, available_capital=1_000_000.0,
        daily_pnl=0.0, daily_loss_limit=50_000.0, peak_capital=1_000_000.0, max_allowed_drawdown=0.20,
        consecutive_losses=0, clock=_clock(),
    )
    decision = evaluate_trade_capital_safety(d1_input, ProposedTradeEffect(250_000.0, 50_000.0), None, clock=_clock())
    assert decision.allowed is False   # 70% already used -> new trade pushes to 95% -> restricted, blocks risky addition


def test_single_trade_acceptable_but_portfolio_unsafe():
    from bujji.trading_brain.risk_governor.capital_safety_governor import (
        ProposedTradeEffect, evaluate_trade_capital_safety,
    )
    empty_snapshot = aggregate_portfolio_risk([], None, None, None, clock=_clock())
    d1_input_empty = portfolio_risk_to_capital_safety_input(
        empty_snapshot, reserved_risk=0.0, total_capital=1_000_000.0, available_capital=1_000_000.0,
        daily_pnl=0.0, daily_loss_limit=50_000.0, peak_capital=1_000_000.0, max_allowed_drawdown=0.20,
        consecutive_losses=0, clock=_clock(),
    )
    # used_margin/open_risk are None (empty book, no margin_snapshot supplied) -> treat as an
    # explicit zero-book scenario instead, matching evaluate_trade_capital_safety's own contract
    d1_input_empty = replace(d1_input_empty, used_margin=0.0, open_risk=0.0)
    decision_alone = evaluate_trade_capital_safety(d1_input_empty, ProposedTradeEffect(250_000.0, 50_000.0), None, clock=_clock())
    assert decision_alone.allowed is True

    heavy_groups = [_group("PG-1", "S1")]
    heavy_snapshot = aggregate_portfolio_risk(heavy_groups, _margin_snapshot(700_000.0), _explanation(), {"PG-1": 300_000.0}, clock=_clock())
    d1_input_heavy = portfolio_risk_to_capital_safety_input(
        heavy_snapshot, reserved_risk=0.0, total_capital=1_000_000.0, available_capital=1_000_000.0,
        daily_pnl=0.0, daily_loss_limit=50_000.0, peak_capital=1_000_000.0, max_allowed_drawdown=0.20,
        consecutive_losses=0, clock=_clock(),
    )
    decision_portfolio = evaluate_trade_capital_safety(d1_input_heavy, ProposedTradeEffect(250_000.0, 50_000.0), None, clock=_clock())
    assert decision_portfolio.allowed is False


def test_explanation_matches_classification():
    groups = [_group("PG-1", "S1"), _group("PG-2", "S2", coid="PG-2-L1")]
    risk_map = {"PG-1": 400_000.0, "PG-2": 100_000.0}
    snapshot = aggregate_portfolio_risk(groups, None, None, risk_map, clock=_clock())
    status, reasons = classify_portfolio_risk(snapshot)
    explanation = explain_portfolio_risk(snapshot, status, reasons)
    assert status == RISK_HIGH_RISK
    assert "high risk" in explanation.lower()
    assert "S1" in explanation   # the actual dominant strategy is named


def test_scenario_engine_adding_hedge():
    """Modeled correctly per Gate B's own established discipline:
    max_loss is always non-negative (a negative figure is malformed
    input, rejected everywhere else in this Governor). "Adding a
    hedge" to an existing strategy means Gate B recomputes THAT SAME
    position group's own max_loss LOWER (e.g. a naked short's defined-
    risk formula producing 400k, versus a hedged/covered version of
    the identical group producing 150k) -- not a second group with a
    fabricated negative risk contribution, which the aggregator
    correctly rejects (see test_negative_risk_figure_rejected)."""
    before_groups = [_group("PG-1", "S1")]
    after_groups = [_group("PG-1", "S1")]   # same group, now hedged
    result = simulate_portfolio_change(
        before_groups, _margin_snapshot(400_000.0), None, {"PG-1": 400_000.0},
        after_groups, _margin_snapshot(420_000.0), None, {"PG-1": 150_000.0},
        clock=_clock(),
    )
    assert result.risk_delta == pytest.approx(-250_000.0)
    assert result.margin_delta == pytest.approx(20_000.0)


# --------------------------------------------------------------------- #
# Integrity
# --------------------------------------------------------------------- #

def test_no_broker_imports():
    source_path = Path(inspect.getfile(pra))
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
    source_path = Path(inspect.getfile(pra))
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
    source_path = Path(inspect.getfile(pra))
    tree = ast.parse(source_path.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offenders = [m for m in imported if "capital_check" in m]
    assert offenders == [], f"must not import capital_check: {offenders}"


def test_module_never_imports_d1_admission_logic_only_the_snapshot_dataclass():
    """One-way dependency, verified via AST (not raw text -- the
    module's own docstring legitimately names
    evaluate_trade_capital_safety in prose explaining what it does NOT
    import, so a plain substring search would falsely fail): this
    module imports CapitalSafetySnapshot (a plain data model) but
    never evaluate_trade_capital_safety/classify_capital_safety (D.1's
    own decision logic) -- confirming Portfolio Aggregator -> Capital
    Safety Governor, never the reverse."""
    source_path = Path(inspect.getfile(pra))
    tree = ast.parse(source_path.read_text())
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
    assert "evaluate_trade_capital_safety" not in imported_names
    assert "classify_capital_safety" not in imported_names
    assert "CapitalSafetySnapshot" in imported_names   # the one thing it SHOULD import


def test_no_threshold_drift():
    # 55% largest-position concentration: CONCENTRATED under default
    # thresholds (concentrated=0.35, high_risk=0.60), but HIGH_RISK under
    # a tighter custom policy (concentrated=0.10, high_risk=0.50) --
    # genuinely different outcomes, proving the thresholds are actually
    # read, not hardcoded.
    custom = PortfolioRiskThresholds(concentrated_threshold=0.10, high_risk_threshold=0.50)
    groups = [_group("PG-1", "S1"), _group("PG-2", "S2", coid="PG-2-L1")]
    snapshot = aggregate_portfolio_risk(groups, None, None, {"PG-1": 450_000.0, "PG-2": 550_000.0}, clock=_clock())
    status_default, _ = classify_portfolio_risk(snapshot)
    status_custom, _ = classify_portfolio_risk(snapshot, custom)
    assert status_default == RISK_CONCENTRATED
    assert status_custom == RISK_HIGH_RISK
    assert status_default != status_custom


def test_no_force_override_parameter():
    sig = inspect.signature(aggregate_portfolio_risk)
    forbidden = {"force", "override", "skip_validation", "bypass"}
    assert forbidden.isdisjoint(sig.parameters.keys())
