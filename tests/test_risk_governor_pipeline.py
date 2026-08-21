import ast
import inspect
from datetime import datetime

import pytest

from bujji.trading_brain.risk_governor.capital_safety_governor import (
    CapitalSafetySnapshot, ProposedTradeEffect,
)
from bujji.trading_brain.risk_governor.risk_budget_governor import RiskPolicy
from bujji.trading_brain.risk_governor.position_lifecycle_intelligence import build_position_risk_snapshot
from bujji.trading_brain.risk_governor.position_group_fold import LegFillState, LegState, PositionGroupState
from bujji.trading_brain.risk_governor.simulated_margin_provider import MarginExplanation
from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginSnapshot
from bujji.trading_brain.risk_governor.adaptive_risk_memory import (
    OUTCOME_LOSS, OUTCOME_WIN, build_risk_memory_entry,
)
from bujji.trading_brain.risk_governor import risk_governor_pipeline as rgp

BASE_TS = datetime(2026, 8, 2, 9, 15, 0)
clock = lambda: BASE_TS


def capital_snapshot(**overrides):
    defaults = dict(
        total_capital=1_000_000.0, available_capital=1_000_000.0, used_margin=200_000.0,
        open_risk=100_000.0, reserved_risk=0.0, daily_pnl=0.0, daily_loss_limit=50_000.0,
        peak_capital=1_000_000.0, max_allowed_drawdown=0.20, consecutive_losses=0, timestamp=BASE_TS,
    )
    defaults.update(overrides)
    return CapitalSafetySnapshot(**defaults)


def position_snapshot(**overrides):
    defaults = dict(
        position_group_id="pg1", strategy_type="IRON_CONDOR", entry_value=0.0, current_value=-1000.0,
        quantity=5, lifecycle_state="OPEN", initial_risk=50000.0, current_risk=50000.0, margin_consumed=20000.0,
    )
    defaults.update(overrides)
    return build_position_risk_snapshot(clock=clock, **defaults)


def _group(pg_id, strategy_id, qty=25):
    coid = f"{pg_id}-L1"
    return PositionGroupState(
        position_group_id=pg_id, lifecycle_state="OPEN", strategy_id=strategy_id,
        legs={coid: LegState(client_order_id=coid, contract_id="C", requested_quantity=None,
                              submit_status="LEG_ACKED",
                              fill=LegFillState(client_order_id=coid, cumulative_filled_quantity=qty))},
    )


def diversified_portfolio_kwargs():
    """4 distinct strategies, 25% concentration each -- satisfies D.2's
    own DIVERSIFIED rule (concentration <= 0.25 AND >= 3 strategies),
    so classify_portfolio_risk never returns INVALID/HIGH_RISK here."""
    # Total risk (20,000) is deliberately kept well within RiskPolicy's
    # default budget (5% of total_capital=1,000,000 = 50,000) so the
    # "healthy path" fixture reaches D.3 with real remaining headroom --
    # individual tests override requested_risk to force a REJECTED case.
    groups = [_group(f"PG-{i}", f"S{i}") for i in range(1, 5)]
    risk_by_position_group_id = {f"PG-{i}": 5_000.0 for i in range(1, 5)}
    margin_snapshot = MarginSnapshot(
        required_margin=40_000.0, margin_verified=True, margin_source="SIM", as_of=BASE_TS, quote=None,
    )
    margin_explanation = MarginExplanation(
        total_required_margin=40_000.0, total_long_exposure=10_000.0, total_short_exposure=30_000.0,
        covered_exposure=0.0, naked_exposure=30_000.0, contributing_legs=(),
        highest_margin_contributor=None, risk_flags=(), risk_classification="LOW_RISK",
        explanation_source="SIM",
    )
    return dict(
        position_groups=groups, margin_snapshot=margin_snapshot, margin_explanation=margin_explanation,
        risk_by_position_group_id=risk_by_position_group_id,
    )


def make_context(**overrides):
    defaults = dict(
        capital_snapshot=capital_snapshot(),
        proposed_trade_effect=ProposedTradeEffect(additional_margin=10000.0, additional_max_loss=5000.0),
        capital_safety_thresholds=None,
        margin_snapshot=None,
        margin_explanation=None,
        risk_by_position_group_id=None,
        position_groups=[],
        portfolio_risk_thresholds=None,
        risk_policy=RiskPolicy(),
        desired_quantity=5,
        requested_risk=5000.0,
        position_snapshot=position_snapshot(),
        position_health_thresholds=None,
        strategy_type="IRON_CONDOR",
        market_regime="SIDEWAYS",
        memory_entries=(),
        clock=clock,
    )
    defaults.update(diversified_portfolio_kwargs())
    defaults.update(overrides)
    return rgp.GovernorPipelineContext(**defaults)


def good_history(n=20, strategy="IRON_CONDOR", regime="SIDEWAYS"):
    return tuple(
        build_risk_memory_entry(
            f"e{i}", strategy, regime, "LOW_VOL", "SAFE", "RISK_HEALTHY", 5, 5,
            OUTCOME_WIN, 0.01, 0.03, 2, "PROFIT_TARGET", clock=clock,
        )
        for i in range(n)
    )


def bad_history(n=20, strategy="IRON_CONDOR", regime="SIDEWAYS"):
    return tuple(
        build_risk_memory_entry(
            f"b{i}", strategy, regime, "LOW_VOL", "SAFE", "RISK_HEALTHY", 5, 5,
            OUTCOME_LOSS, 0.15, 0.00, 5, "MAX_LOSS", clock=clock,
        )
        for i in range(n)
    )


# --------------------------------------------------------------------- #
# Memory / execution order
# --------------------------------------------------------------------- #

def test_execution_order_matches_stage_order_constant():
    context = make_context()
    result = rgp.run_risk_governor_pipeline(context)
    assert [s.stage for s in result.decision_trace.steps] == list(rgp.STAGE_ORDER)


def test_execution_order_stable_across_repeated_runs():
    context = make_context()
    r1 = rgp.run_risk_governor_pipeline(context)
    r2 = rgp.run_risk_governor_pipeline(context)
    assert [s.stage for s in r1.decision_trace.steps] == [s.stage for s in r2.decision_trace.steps]


# --------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------- #

def test_healthy_path_reaches_final_approved():
    context = make_context(memory_entries=())
    result = rgp.run_risk_governor_pipeline(context)
    assert result.final_status == rgp.PIPELINE_APPROVED
    assert result.blocking_stage is None
    assert result.final_quantity > 0
    assert result.capital_decision is not None
    assert result.portfolio_decision is not None
    assert result.portfolio_decision.status != "INVALID"
    assert result.budget_decision is not None
    assert result.admission_decision is not None
    assert result.adaptive_decision is not None


def test_blocked_at_capital_stops_pipeline_early():
    blocked_snapshot = capital_snapshot(daily_pnl=-60000.0)  # exceeds daily_loss_limit -> BLOCKED after
    context = make_context(capital_snapshot=blocked_snapshot)
    result = rgp.run_risk_governor_pipeline(context)
    assert result.final_status == rgp.PIPELINE_BLOCKED
    assert result.blocking_stage == rgp.STAGE_CAPITAL
    assert result.final_quantity == 0
    assert result.portfolio_decision is None
    assert result.budget_decision is None
    assert result.admission_decision is None
    assert result.adaptive_decision is None
    assert [s.stage for s in result.decision_trace.steps] == [rgp.STAGE_CAPITAL]


def test_blocked_at_portfolio_on_invalid_data():
    # Empty portfolio: D.2 itself reports RISK_INVALID (no positions to
    # compute concentration from) -- its own existing "cannot be
    # trusted" signal, not a new rule invented by this pipeline.
    context = make_context(
        position_groups=[], margin_snapshot=None, margin_explanation=None, risk_by_position_group_id=None,
    )
    result = rgp.run_risk_governor_pipeline(context)
    assert result.final_status == rgp.PIPELINE_BLOCKED
    assert result.blocking_stage == rgp.STAGE_PORTFOLIO
    assert result.budget_decision is None
    assert result.admission_decision is None
    assert result.adaptive_decision is None


def test_blocked_at_budget_on_rejected():
    context = make_context(
        requested_risk=999999.0,  # wildly exceeds the fixture's 30,000 remaining budget
    )
    result = rgp.run_risk_governor_pipeline(context)
    assert result.final_status == rgp.PIPELINE_BLOCKED
    assert result.blocking_stage == rgp.STAGE_BUDGET
    assert result.admission_decision is None
    assert result.adaptive_decision is None
    assert result.final_quantity == 0


def test_critical_position_reaches_admission_but_does_not_block_new_risk_action():
    critical_position = position_snapshot(entry_value=0.0, current_value=-44000.0, initial_risk=50000.0, current_risk=50000.0)
    context = make_context(position_snapshot=critical_position)
    result = rgp.run_risk_governor_pipeline(context)
    # CRITICAL health -> EXIT_CONSIDERATION, not BLOCK_NEW_RISK -- the
    # pipeline reaches and passes the ADMISSION stage; only D.4's own
    # recommended action differs, which is a genuinely different case
    # from a block.
    assert result.blocking_stage is None
    assert result.admission_decision.action == "EXIT_CONSIDERATION"


def test_blocked_at_admission_when_position_data_invalid():
    # initial_risk=None makes D.4's own classify_position_health()
    # return INVALID, whose recommend_risk_action() maps to
    # BLOCK_NEW_RISK -- reached here because capital/portfolio/budget
    # are otherwise healthy.
    invalid_position = position_snapshot(initial_risk=None, current_risk=None, entry_value=None, current_value=None)
    context = make_context(position_snapshot=invalid_position)
    result = rgp.run_risk_governor_pipeline(context)
    assert result.final_status == rgp.PIPELINE_BLOCKED
    assert result.blocking_stage == rgp.STAGE_ADMISSION
    assert result.adaptive_decision is None
    assert result.final_quantity == 0


def test_adaptive_reduction_lowers_final_quantity_below_base():
    context = make_context(memory_entries=bad_history())
    result = rgp.run_risk_governor_pipeline(context)
    assert result.final_status == rgp.PIPELINE_APPROVED
    base = result.budget_decision.sizing.recommended_quantity
    assert result.final_quantity < base
    assert result.adaptive_decision.adaptive_recommendation in (
        "REDUCE_SIZE_10", "REDUCE_SIZE_20", "REDUCE_SIZE_30",
    )


def test_adaptive_increase_never_exceeds_d3_maximum():
    context = make_context(memory_entries=good_history(), desired_quantity=1, requested_risk=1000.0)
    result = rgp.run_risk_governor_pipeline(context)
    assert result.final_status == rgp.PIPELINE_APPROVED
    assert result.adaptive_decision.adaptive_recommendation in ("INCREASE_SIZE_10", "INCREASE_SIZE_20")
    assert result.final_quantity <= result.budget_decision.sizing.maximum_quantity


def test_explanation_blocked():
    context = make_context(capital_snapshot=capital_snapshot(daily_pnl=-60000.0))
    result = rgp.run_risk_governor_pipeline(context)
    text = rgp.explain_pipeline_result(result)
    assert "BLOCKED at CAPITAL" in text


def test_explanation_approved_unchanged():
    context = make_context(memory_entries=())
    result = rgp.run_risk_governor_pipeline(context)
    text = rgp.explain_pipeline_result(result)
    assert "APPROVED" in text
    assert str(result.final_quantity) in text


def test_explanation_approved_adjusted():
    context = make_context(memory_entries=bad_history())
    result = rgp.run_risk_governor_pipeline(context)
    text = rgp.explain_pipeline_result(result)
    assert "adjusted" in text
    assert str(result.final_quantity) in text


# --------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------- #

def test_no_duplicated_threshold_constants_in_pipeline_module():
    # The pipeline module itself must define no numeric threshold
    # constant of its own -- every threshold lives in D.1-D.5.
    tree = ast.parse(open(rgp.__file__).read())
    module_level_assigns = [node for node in tree.body if isinstance(node, ast.Assign)]
    numeric_constants = []
    for node in module_level_assigns:
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)) and not isinstance(node.value.value, bool):
            numeric_constants.append(node.value.value)
    assert numeric_constants == [], numeric_constants


def test_no_broker_execution_or_order_imports():
    tree = ast.parse(open(rgp.__file__).read())
    forbidden = ("broker", "fyers", "order", "execution")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(f in alias.name.lower() for f in forbidden)
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").lower()
            assert not any(f in mod for f in forbidden)


def test_no_database_write_imports():
    tree = ast.parse(open(rgp.__file__).read())
    forbidden = ("sqlite3", "psycopg", "sqlalchemy", "pymongo")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(f in alias.name.lower() for f in forbidden)


def test_deterministic_execution_same_input_same_output():
    context = make_context(memory_entries=good_history())
    r1 = rgp.run_risk_governor_pipeline(context)
    r2 = rgp.run_risk_governor_pipeline(context)
    assert r1.final_status == r2.final_status
    assert r1.final_quantity == r2.final_quantity
    assert r1.blocking_stage == r2.blocking_stage


def test_no_force_override_bypass_parameter():
    sig = inspect.signature(rgp.run_risk_governor_pipeline)
    for name in sig.parameters:
        assert "force" not in name.lower()
        assert "override" not in name.lower()
        assert "bypass" not in name.lower()


# --------------------------------------------------------------------- #
# Bug fix regression: hedge trades (negative requested_risk) must never
# crash the pipeline. assess_trade_risk_budget() approves them
# unconditionally; calculate_safe_position_size() explicitly rejects
# negative input by design -- D.6 must branch, never call both blindly.
# --------------------------------------------------------------------- #

def test_hedge_trade_negative_requested_risk_does_not_crash_and_is_approved():
    context = make_context(requested_risk=-500.0, desired_quantity=1)
    result = rgp.run_risk_governor_pipeline(context)  # must not raise
    assert result.final_status == rgp.PIPELINE_APPROVED
    assert result.blocking_stage is None
    assert result.budget_decision.decision.status == "APPROVED"
    assert result.budget_decision.sizing.recommended_quantity == 1
    assert result.budget_decision.sizing.reduction_required is False
    assert "Hedge" in result.budget_decision.sizing.reason


def test_hedge_trade_still_blocked_upstream_by_capital():
    context = make_context(requested_risk=-500.0, desired_quantity=1,
                            capital_snapshot=capital_snapshot(daily_pnl=-60000.0))
    result = rgp.run_risk_governor_pipeline(context)  # must not raise
    assert result.blocking_stage == rgp.STAGE_CAPITAL
