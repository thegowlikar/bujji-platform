import ast
import math
from datetime import datetime, timedelta

import pytest

from bujji.trading_brain.risk_governor.capital_safety_governor import SAFETY_BLOCKED, SAFETY_CAUTION, SAFETY_RESTRICTED, SAFETY_SAFE
from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import RISK_CONCENTRATED, RISK_DIVERSIFIED, RISK_HEALTHY, RISK_HIGH_RISK
from bujji.trading_brain.risk_governor import position_lifecycle_intelligence as pli


def clock_at(ts):
    return lambda: ts


BASE_TS = datetime(2026, 8, 1, 10, 0, 0)


def snap(
    position_group_id="pg1", strategy_type="IRON_CONDOR", entry_value=-50000.0, current_value=-50000.0,
    quantity=1, lifecycle_state="OPEN", initial_risk=50000.0, current_risk=50000.0, margin_consumed=20000.0,
    ts=BASE_TS,
):
    return pli.build_position_risk_snapshot(
        position_group_id, strategy_type, entry_value, current_value, quantity, lifecycle_state,
        initial_risk, current_risk, margin_consumed, clock=clock_at(ts),
    )


# --------------------------------------------------------------------- #
# Position monitoring
# --------------------------------------------------------------------- #

def test_max_loss_remaining_formula_matches_worked_examples():
    s1 = snap(entry_value=0.0, current_value=-20000.0, initial_risk=50000.0)
    assert s1.max_loss_remaining == 30000.0
    s2 = snap(entry_value=0.0, current_value=0.0, initial_risk=50000.0)
    assert s2.max_loss_remaining == 50000.0
    s3 = snap(entry_value=0.0, current_value=10000.0, initial_risk=50000.0)
    assert s3.max_loss_remaining == 60000.0


def test_max_loss_remaining_can_go_negative_on_breach():
    s = snap(entry_value=0.0, current_value=-60000.0, initial_risk=50000.0)
    assert s.max_loss_remaining == -10000.0


def test_missing_current_value_leaves_pnl_and_remaining_none():
    s = snap(current_value=None)
    assert s.unrealized_pnl is None
    assert s.max_loss_remaining is None


def test_negative_quantity_rejected():
    with pytest.raises(pli.IllegalPositionRiskInputError):
        snap(quantity=-1)


def test_invalid_lifecycle_state_rejected():
    with pytest.raises(pli.IllegalPositionRiskInputError):
        snap(lifecycle_state="MINTED")


def test_negative_initial_risk_rejected():
    with pytest.raises(pli.IllegalPositionRiskInputError):
        snap(initial_risk=-1.0)


def test_health_classification_progression():
    healthy = snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=-5000.0)
    watch = snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=-16000.0)
    stressed = snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=-32000.0)
    critical = snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=-44000.0)
    assert pli.classify_position_health(healthy)[0] == pli.HEALTH_HEALTHY
    assert pli.classify_position_health(watch)[0] == pli.HEALTH_WATCH
    assert pli.classify_position_health(stressed)[0] == pli.HEALTH_STRESSED
    assert pli.classify_position_health(critical)[0] == pli.HEALTH_CRITICAL


def test_health_classification_invalid_on_missing_data():
    s = snap(current_value=None, initial_risk=None)
    status, reasons = pli.classify_position_health(s)
    assert status == pli.HEALTH_INVALID
    assert reasons


def test_health_classification_invalid_on_zero_initial_risk():
    s = snap(initial_risk=0.0, current_value=0.0, entry_value=0.0)
    status, _ = pli.classify_position_health(s)
    assert status == pli.HEALTH_INVALID


def test_risk_change_report_detects_increasing_risk_and_severity():
    prev = snap(current_risk=50000.0, margin_consumed=20000.0, current_value=-1000.0)
    curr = snap(current_risk=68000.0, margin_consumed=24000.0, current_value=-3000.0)
    report = pli.evaluate_position_risk_change(prev, curr)
    assert report.risk_increasing is True
    assert report.margin_expanding is True
    assert report.severity in (pli.SEVERITY_MATERIAL, pli.SEVERITY_SEVERE)


def test_risk_change_report_detects_loss_accelerating():
    prev = snap(entry_value=0.0, current_value=-1000.0)
    curr = snap(entry_value=0.0, current_value=-9000.0)
    report = pli.evaluate_position_risk_change(prev, curr)
    assert report.loss_accelerating is True


def test_risk_change_report_no_loss_accelerating_when_pnl_positive():
    prev = snap(entry_value=0.0, current_value=1000.0)
    curr = snap(entry_value=0.0, current_value=5000.0)
    report = pli.evaluate_position_risk_change(prev, curr)
    assert report.loss_accelerating is False


def test_risk_change_report_missing_data_leaves_deltas_none():
    prev = snap(current_risk=None)
    curr = snap(current_risk=50000.0)
    report = pli.evaluate_position_risk_change(prev, curr)
    assert report.risk_delta is None
    assert report.risk_increasing is None


# --------------------------------------------------------------------- #
# Portfolio interaction (Part 5's worked examples)
# --------------------------------------------------------------------- #

def test_stressed_position_with_healthy_portfolio_gets_add_hedge():
    stressed = snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=-32000.0)
    rec = pli.recommend_risk_action(stressed, SAFETY_SAFE, RISK_HEALTHY, None, clock_at(BASE_TS))
    assert rec.action == pli.ACTION_ADD_HEDGE


def test_stressed_position_with_concentrated_portfolio_gets_reduce_size():
    stressed = snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=-32000.0)
    rec = pli.recommend_risk_action(stressed, SAFETY_SAFE, RISK_CONCENTRATED, None, clock_at(BASE_TS))
    assert rec.action == pli.ACTION_REDUCE_SIZE


def test_stressed_position_with_restricted_account_gets_reduce_size_even_if_portfolio_healthy():
    stressed = snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=-32000.0)
    rec = pli.recommend_risk_action(stressed, SAFETY_RESTRICTED, RISK_HEALTHY, None, clock_at(BASE_TS))
    assert rec.action == pli.ACTION_REDUCE_SIZE


def test_blocked_account_forces_block_new_risk_regardless_of_position_health():
    healthy = snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=0.0)
    rec = pli.recommend_risk_action(healthy, SAFETY_BLOCKED, RISK_HEALTHY, None, clock_at(BASE_TS))
    assert rec.action == pli.ACTION_BLOCK_NEW_RISK


def test_critical_position_always_gets_exit_consideration_even_with_healthy_portfolio():
    critical = snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=-44000.0)
    rec = pli.recommend_risk_action(critical, SAFETY_SAFE, RISK_HEALTHY, None, clock_at(BASE_TS))
    assert rec.action == pli.ACTION_EXIT_CONSIDERATION


def test_healthy_position_gets_hold():
    healthy = snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=0.0)
    rec = pli.recommend_risk_action(healthy, SAFETY_SAFE, RISK_HEALTHY, None, clock_at(BASE_TS))
    assert rec.action == pli.ACTION_HOLD


def test_watch_position_gets_monitor():
    watch = snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=-16000.0)
    rec = pli.recommend_risk_action(watch, SAFETY_SAFE, RISK_HEALTHY, None, clock_at(BASE_TS))
    assert rec.action == pli.ACTION_MONITOR


def test_hedge_adding_reduces_current_risk_via_scenario():
    result = pli.simulate_position_scenario(
        "pg1", "IRON_CONDOR", "OPEN",
        0.0, -30000.0, 1, 50000.0, 50000.0, 20000.0,
        0.0, -30000.0, 1, 50000.0, 32500.0, 22000.0,
        clock_at(BASE_TS),
    )
    assert result.risk_impact == "REDUCED"


# --------------------------------------------------------------------- #
# Scenario engine
# --------------------------------------------------------------------- #

def test_scenario_price_movement_increases_risk():
    result = pli.simulate_position_scenario(
        "pg1", "IRON_CONDOR", "OPEN",
        0.0, -10000.0, 1, 50000.0, 50000.0, 20000.0,
        0.0, -10000.0, 1, 50000.0, 60000.0, 20000.0,
        clock_at(BASE_TS),
    )
    assert result.risk_impact == "INCREASED"
    assert result.before.current_risk == 50000.0
    assert result.after.current_risk == 60000.0


def test_scenario_unchanged_when_current_risk_same():
    result = pli.simulate_position_scenario(
        "pg1", "IRON_CONDOR", "OPEN",
        0.0, -10000.0, 1, 50000.0, 50000.0, 20000.0,
        0.0, -15000.0, 1, 50000.0, 50000.0, 20000.0,
        clock_at(BASE_TS),
    )
    assert result.risk_impact == "UNCHANGED"


def test_scenario_unavailable_when_current_risk_missing():
    result = pli.simulate_position_scenario(
        "pg1", "IRON_CONDOR", "OPEN",
        0.0, -10000.0, 1, 50000.0, None, 20000.0,
        0.0, -10000.0, 1, 50000.0, 50000.0, 20000.0,
        clock_at(BASE_TS),
    )
    assert result.risk_impact == "UNAVAILABLE"


def test_scenario_never_calls_broker_or_predicts_market_prices():
    # Purely a measurement between two caller-supplied observations --
    # the function signature accepts no broker/client/quote-fetch
    # argument at all, verified structurally.
    import inspect
    sig = inspect.signature(pli.simulate_position_scenario)
    for name in sig.parameters:
        assert "broker" not in name.lower()
        assert "client" not in name.lower()


# --------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------- #

def test_invalid_health_forces_block_new_risk_not_hold():
    invalid_snap = snap(initial_risk=None, current_value=None)
    rec = pli.recommend_risk_action(invalid_snap, SAFETY_SAFE, RISK_HEALTHY, None, clock_at(BASE_TS))
    assert rec.action == pli.ACTION_BLOCK_NEW_RISK


def test_no_force_override_parameter_on_any_public_function():
    import inspect
    for fn in (
        pli.build_position_risk_snapshot, pli.evaluate_position_risk_change, pli.classify_position_health,
        pli.recommend_risk_action, pli.simulate_position_scenario,
    ):
        sig = inspect.signature(fn)
        for name in sig.parameters:
            assert "force" not in name.lower()
            assert "override" not in name.lower()
            assert "bypass" not in name.lower()


def test_duplicate_event_id_rejected():
    store = pli.RiskObservationStore()
    event = pli.RiskObservationEvent(
        event_id="evt1", event_type=pli.EVENT_POSITION_OPENED, timestamp=BASE_TS,
        position_group_id="pg1", previous_state=None, new_state="OPEN", reason="opened",
    )
    store.record_event(event)
    with pytest.raises(pli.DuplicateRiskObservationEventError):
        store.record_event(event)


def test_store_has_no_update_or_delete_method():
    store = pli.RiskObservationStore()
    assert not hasattr(store, "update_event")
    assert not hasattr(store, "delete_event")
    assert not hasattr(store, "remove_event")


def test_negative_margin_consumed_rejected():
    with pytest.raises(pli.IllegalPositionRiskInputError):
        snap(margin_consumed=-1.0)


# --------------------------------------------------------------------- #
# Integrity
# --------------------------------------------------------------------- #

def test_no_broker_or_execution_imports_ast():
    import bujji.trading_brain.risk_governor.position_lifecycle_intelligence as module
    tree = ast.parse(open(module.__file__).read())
    forbidden = ("broker", "fyers", "order", "execution")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(f in alias.name.lower() for f in forbidden), alias.name
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").lower()
            assert not any(f in mod for f in forbidden), node.module


def test_never_imports_exit_engine_or_capital_check():
    import bujji.trading_brain.risk_governor.position_lifecycle_intelligence as module
    tree = ast.parse(open(module.__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = (node.module or "")
            assert "exit_engine" not in mod
            assert "capital_check" not in mod


def test_never_imports_d1_d2_decision_functions_only_data_and_constants():
    import bujji.trading_brain.risk_governor.position_lifecycle_intelligence as module
    tree = ast.parse(open(module.__file__).read())
    forbidden_names = (
        "evaluate_trade_capital_safety", "classify_capital_safety",
        "aggregate_portfolio_risk", "classify_portfolio_risk",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in forbidden_names


def test_explanation_text_reflects_action_for_all_branches():
    cases = [
        (snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=0.0), SAFETY_SAFE, RISK_HEALTHY, pli.ACTION_HOLD),
        (snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=-16000.0), SAFETY_SAFE, RISK_HEALTHY, pli.ACTION_MONITOR),
        (snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=-32000.0), SAFETY_SAFE, RISK_HEALTHY, pli.ACTION_ADD_HEDGE),
        (snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=-32000.0), SAFETY_SAFE, RISK_CONCENTRATED, pli.ACTION_REDUCE_SIZE),
        (snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=-44000.0), SAFETY_SAFE, RISK_HEALTHY, pli.ACTION_EXIT_CONSIDERATION),
        (snap(initial_risk=50000.0, current_risk=50000.0, entry_value=0.0, current_value=0.0), SAFETY_BLOCKED, RISK_HEALTHY, pli.ACTION_BLOCK_NEW_RISK),
    ]
    for position, capital_status, portfolio_status, expected_action in cases:
        rec = pli.recommend_risk_action(position, capital_status, portfolio_status, None, clock_at(BASE_TS))
        assert rec.action == expected_action
        assert rec.explanation.strip() != ""


def test_risk_change_severity_only_fires_on_risk_increase_never_decrease():
    import random
    random.seed(11)
    for _ in range(100):
        prev_risk = random.uniform(1000, 100000)
        delta_fraction = random.uniform(-0.5, 0.5)
        curr_risk = max(0.0, prev_risk * (1 + delta_fraction))
        prev = snap(initial_risk=prev_risk, current_risk=prev_risk)
        curr = snap(initial_risk=prev_risk, current_risk=curr_risk)
        report = pli.evaluate_position_risk_change(prev, curr)
        if report.severity != pli.SEVERITY_NONE:
            assert report.risk_delta is not None and report.risk_delta > 0
