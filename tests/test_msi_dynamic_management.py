"""Tests for bujji.msi_dynamic_management (Series 109)."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from bujji.msi_dynamic_management import engine, taxonomy, query, runner
from bujji.msi_strategy_selection_foundation import taxonomy as ssf_taxonomy


def _lc(state, compatible, fired=()):
    return SimpleNamespace(
        lifecycle_id=f"LC-{state}-{compatible}-{fired}", position_state=state,
        thesis_invalidation=SimpleNamespace(compatible=compatible),
        adjustment_policy=SimpleNamespace(fired=fired),
    )


TS = "2026-05-25T15:15:00"


def test_six_decision_types_are_produced_independently_never_combined():
    board = runner.run_dynamic_management(
        lifecycle=_lc("HEALTHY", True), strategy_family="IRON_CONDOR",
        short_strike_delta=0.10, dte=30, portfolio_delta_after=50.0, portfolio_vega_after=500.0,
        entry_volatility_regime="STABLE", current_volatility_regime="STABLE", timestamp=TS,
    )
    decisions = [board.strike_roll, board.expiry_roll, board.delta_rebalance,
                board.wing_adjustment, board.strategy_conversion, board.full_exit]
    assert {d.decision_type for d in decisions} == set(taxonomy.ALL_DECISION_TYPES)
    assert len({d.assessment_id for d in decisions}) == 6  # each has its own, distinct assessment_id


def test_priority_is_evidence_computed_not_fixed():
    """The SAME decision type (strike_roll) must land at different
    priorities purely as a function of real evidence -- never a static
    per-decision-type priority."""
    lc = _lc("HEALTHY", True)
    safe = engine.assess_strike_roll(lc, 0.10, timestamp=TS)
    tested = engine.assess_strike_roll(lc, 0.40, timestamp=TS)
    mandatory = engine.assess_strike_roll(lc, 0.55, timestamp=TS)
    assert safe.priority == taxonomy.PRIORITY_AVOID
    assert tested.priority == taxonomy.PRIORITY_RECOMMENDED
    assert mandatory.priority == taxonomy.PRIORITY_MANDATORY


def test_broken_thesis_routes_to_exit_not_strike_or_expiry_roll():
    lc = _lc("THESIS_BROKEN", False)
    strike = engine.assess_strike_roll(lc, 0.40, timestamp=TS)
    expiry = engine.assess_expiry_roll(lc, 10, timestamp=TS)
    exit_ = engine.assess_full_exit(lc, timestamp=TS)
    assert strike.priority == taxonomy.PRIORITY_AVOID
    assert expiry.priority == taxonomy.PRIORITY_AVOID
    assert exit_.recommended is True


def test_delta_rebalance_reuses_series96_watch_thresholds_by_identity():
    from bujji.msi_position_lifecycle import config as pli_config
    lc = _lc("HEALTHY", True)
    below = engine.assess_delta_rebalance(lc, pli_config.WATCH_ABS_PORTFOLIO_DELTA - 1, 0.0, timestamp=TS)
    above = engine.assess_delta_rebalance(lc, pli_config.WATCH_ABS_PORTFOLIO_DELTA + 1, 0.0, timestamp=TS)
    assert below.priority == taxonomy.PRIORITY_AVOID
    assert above.priority == taxonomy.PRIORITY_RECOMMENDED


def test_delta_rebalance_mandatory_on_fired_hard_trigger():
    lc = _lc("AT_RISK", True, fired=("HARD_DELTA_BREACH",))
    result = engine.assess_delta_rebalance(lc, 0.0, 0.0, timestamp=TS)
    assert result.priority == taxonomy.PRIORITY_MANDATORY


def test_wing_adjustment_fires_only_on_a_real_regime_change():
    lc = _lc("HEALTHY", True)
    same = engine.assess_wing_adjustment(lc, "STABLE", "STABLE", timestamp=TS)
    changed = engine.assess_wing_adjustment(lc, "STABLE", "ELEVATED", timestamp=TS)
    assert same.priority == taxonomy.PRIORITY_AVOID
    assert changed.priority == taxonomy.PRIORITY_RECOMMENDED


def test_transition_reports_non_representable_honestly():
    result = engine.assess_strategy_transition("VOLATILITY_EXPANSION", timestamp=TS)
    assert result.representable is False
    assert result.to_family is None
    assert "no representable transition rule exists" in result.reasoning[0]


def test_all_transition_rules_target_real_taxonomy_families():
    for rule in taxonomy.TRANSITION_RULES:
        assert rule["from_family"] in ssf_taxonomy.ALL_STRATEGY_FAMILIES
        assert rule["to_family"] in ssf_taxonomy.ALL_STRATEGY_FAMILIES


def test_strategy_conversion_gated_by_roll_whole_strategy_signal():
    from bujji.msi_strategy_optimization import engine as mso_engine
    lc_healthy = _lc("HEALTHY", True)
    roll_no = mso_engine.assess_roll(lc_healthy, short_strike_delta=0.1, dte=30, timestamp=TS)
    assert roll_no.roll_whole_strategy is False
    result = engine.assess_strategy_conversion(lc_healthy, "IRON_CONDOR", roll_no, timestamp=TS)
    assert result.priority == taxonomy.PRIORITY_AVOID


def test_every_decision_explains_all_five_required_facets():
    board = runner.run_dynamic_management(
        lifecycle=_lc("HEALTHY", True), strategy_family="IRON_CONDOR",
        short_strike_delta=0.40, dte=3, portfolio_delta_after=50.0, portfolio_vega_after=500.0,
        entry_volatility_regime="STABLE", current_volatility_regime="ELEVATED", timestamp=TS,
    )
    for d in (board.strike_roll, board.expiry_roll, board.delta_rebalance,
             board.wing_adjustment, board.strategy_conversion, board.full_exit):
        e = d.explanation
        assert len(e.why) > 0
        assert len(e.why_now) > 0
        assert len(e.why_not_later) > 0
        assert len(e.why_not_another_roll) > 0
        assert len(e.why_not_exit) > 0


def test_rerun_is_byte_identical_ids_and_explanations():
    def make():
        return runner.run_dynamic_management(
            lifecycle=_lc("HEALTHY", True), strategy_family="IRON_CONDOR",
            short_strike_delta=0.40, dte=15, portfolio_delta_after=50.0, portfolio_vega_after=500.0,
            entry_volatility_regime="STABLE", current_volatility_regime="STABLE", timestamp=TS,
        )
    b1, b2 = make(), make()
    for field in ("strike_roll", "expiry_roll", "delta_rebalance", "wing_adjustment", "strategy_conversion", "full_exit"):
        d1, d2 = getattr(b1, field), getattr(b2, field)
        assert d1.assessment_id == d2.assessment_id
        assert d1.priority == d2.priority
        assert d1.explanation == d2.explanation


def test_highest_priority_decision_is_deterministic_and_sensible():
    board = runner.run_dynamic_management(
        lifecycle=_lc("AT_RISK", True, fired=("HARD_DELTA_BREACH",)), strategy_family="IRON_CONDOR",
        short_strike_delta=0.10, dte=30, portfolio_delta_after=400.0, portfolio_vega_after=500.0,
        entry_volatility_regime="STABLE", current_volatility_regime="STABLE", timestamp=TS,
    )
    top = query.highest_priority_decision(board)
    assert top.decision_type == taxonomy.DECISION_DELTA_REBALANCE
    assert top.priority == taxonomy.PRIORITY_MANDATORY


def test_package_never_imports_execution_or_broker():
    pkg_dir = Path("bujji/msi_dynamic_management")
    forbidden = {"bujji.execution", "bujji.broker", "fyers_apiv3"}
    for py_file in pkg_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not any(node.module.startswith(f) for f in forbidden), (py_file, node.module)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name.startswith(f) for f in forbidden), (py_file, alias.name)
