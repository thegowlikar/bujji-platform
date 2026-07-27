"""Tests for bujji.msi_performance_analytics — Engineering Series 101."""
from __future__ import annotations

import ast
import os

import pytest

from bujji.msi_performance_analytics import config as pa_config
from bujji.msi_performance_analytics import engine, taxonomy
from bujji.msi_performance_analytics.journal import PerformanceAnalyticsJournal
from bujji.msi_performance_analytics.runner import PerformanceAnalyticsStream, build_trade_analytics_batch
from bujji.msi_performance_analytics import query as pa_query
from bujji.msi_performance_analytics import serialization as pa_serialization
from bujji.msi_decision_auditor.models import DecisionExplanation, DecisionRecord
from bujji.msi_shadow_trading.models import Explanation as StExplanation, ShadowPosition
from bujji.msi_trade_thesis.models import Explanation as ThesisExplanation, TradeThesisAssessment

TS = "2026-05-25T15:30:00+05:30"


def _thesis(thesis_type, conviction="HIGH", direction="STRONG_BULLISH"):
    exp = ThesisExplanation(assessment_id="t1", why_this_thesis=(), supporting_evidence=(), conflicting_evidence=(),
                            what_would_invalidate=(), schema_version="1.0.0")
    return TradeThesisAssessment(
        assessment_id="t1", timestamp=TS, thesis_type=thesis_type, market_expectation="m", expected_move=1.0,
        expected_time_horizon="NEXT_SESSION", volatility_expectation="STABLE", directional_expectation=direction,
        conviction=conviction, invalidation_conditions=(), supporting_domains=(), conflicting_domains=(),
        explanation=exp, provenance="p", schema_version="1.0.0",
    )


def _shadow_position(pnl, reason, structure="LONG_DIRECTIONAL (SINGLE_LEG)"):
    exp = StExplanation("x", (), (), (), "1.0.0")
    return ShadowPosition(
        shadow_trade_id=f"s-{pnl}", decision_id="d1", execution_plan_id="p1", entry_time=TS,
        entry_date="2026-05-25", entry_price=-64.8, entry_structure=structure, entry_legs=(),
        position_close_date="2026-05-26", simulated_margin=8400.0, lifecycle_state=reason,
        realised_pnl=pnl, unrealised_pnl=pnl, exit_time="t2", exit_reason=reason, completed=True,
        explanation=exp, provenance="p", schema_version="1.0.0",
    )


def _decision(family=None, outcome="NO_TRADE", direction="NEUTRAL", confidence="HIGH", vol_state="STABLE"):
    exp = DecisionExplanation(assessment_id="d1", why=(), schema_version="1.0.0")
    return DecisionRecord(
        decision_id=f"d-{id(object())}", timestamp=TS, date="2026-05-25", observation_ids=(), episode_ids=(),
        market_direction=direction, consensus="WEAK_CONSENSUS", volatility_state=vol_state,
        trade_thesis=_thesis("RANGE_PERSISTENCE", confidence, direction), strategy_family=family,
        position_construction=None, portfolio_decision=None, lifecycle_state=None, margin_assessment=None,
        execution_plan=None, confidence=confidence, decision_outcome=outcome, explanation=exp,
        provenance="p", schema_version="1.0.0",
    )


# --- Deliverable 2: TradeAnalytics -----------------------------------------

def test_trade_analytics_computes_real_mfe_mae_from_daily_marks():
    pos = _shadow_position(-4848.75, "THESIS_BROKEN")
    ta = engine.build_trade_analytics(pos, [-100.0, -2000.0, -4848.75], 24000.0, 23900.0)
    assert ta.max_favourable_excursion == -100.0
    assert ta.max_adverse_excursion == -4848.75
    assert ta.holding_period_days == 3
    assert ta.realised_direction == "DOWN"


def test_trade_analytics_return_pct_uses_real_margin():
    pos = _shadow_position(4200.0, "PROFIT_HARVEST")
    ta = engine.build_trade_analytics(pos, [1000.0, 4200.0], 24000.0, 24100.0)
    assert ta.realised_return_pct == pytest.approx(50.0)


def test_trade_analytics_handles_no_margin_honestly():
    pos = _shadow_position(4200.0, "PROFIT_HARVEST")
    pos = pos.__class__(**{**pos.__dict__, "simulated_margin": None})
    ta = engine.build_trade_analytics(pos, [4200.0], 24000.0, 24100.0)
    assert ta.realised_return_pct is None


def test_exit_efficiency_only_meaningful_when_mfe_positive():
    pos = _shadow_position(-500.0, "THESIS_BROKEN")
    ta = engine.build_trade_analytics(pos, [-100.0, -500.0], 24000.0, 23950.0)
    assert ta.exit_efficiency is None  # MFE never positive.


# --- Deliverable 3: decision categorization ---------------------------------

def test_categorize_no_trade_when_no_family():
    d = _decision(family=None)
    assert engine.categorize_decision(d) == taxonomy.CATEGORY_NO_TRADE


def test_categorize_approved():
    d = _decision(family="LONG_DIRECTIONAL", outcome="TRADE_APPROVED")
    assert engine.categorize_decision(d) == taxonomy.CATEGORY_APPROVED


def test_categorize_rejected_when_family_selected_but_no_trade():
    d = _decision(family="SHORT_DIRECTIONAL", outcome="NO_TRADE")
    assert engine.categorize_decision(d) == taxonomy.CATEGORY_REJECTED


def test_decision_category_summary_counts_distributions():
    decisions = [_decision(family=None), _decision(family="LONG_DIRECTIONAL", outcome="TRADE_APPROVED")]
    summary = engine.build_decision_category_summary(decisions, taxonomy.CATEGORY_NO_TRADE)
    assert summary.frequency == 1
    assert summary.category == taxonomy.CATEGORY_NO_TRADE


# --- Deliverable 4/7: edge validation, reliability always disclosed --------

def test_edge_report_flags_small_sample_not_reliable():
    trades = [engine.build_trade_analytics(_shadow_position(-100.0 * i, "THESIS_BROKEN"), [-100.0 * i], 24000.0, 23900.0) for i in range(1, 6)]
    report = engine.build_edge_validation_report(trades, timestamp=TS)
    assert report.win_rate.sample_size == 5
    assert report.win_rate.reliability.reliability == taxonomy.RELIABILITY_NOT_RELIABLE


def test_edge_report_reliable_with_enough_samples():
    trades = [engine.build_trade_analytics(_shadow_position(100.0, "PROFIT_HARVEST"), [100.0], 24000.0, 24100.0) for _ in range(pa_config.MIN_RELIABLE_SAMPLE_SIZE)]
    report = engine.build_edge_validation_report(trades, timestamp=TS)
    assert report.win_rate.reliability.reliability == taxonomy.RELIABILITY_RELIABLE
    assert report.win_rate.value == 1.0


def test_win_rate_confidence_interval_present():
    trades = [engine.build_trade_analytics(_shadow_position(100.0 if i % 2 == 0 else -50.0, "X"), [100.0], 24000.0, 24100.0) for i in range(10)]
    report = engine.build_edge_validation_report(trades, timestamp=TS)
    assert report.win_rate.confidence_interval is not None
    lo, hi = report.win_rate.confidence_interval
    assert 0.0 <= lo <= report.win_rate.value <= hi <= 1.0


def test_expectancy_matches_real_mean_pnl():
    trades = [engine.build_trade_analytics(_shadow_position(p, "X"), [p], 24000.0, 24100.0) for p in (100.0, -50.0, 200.0)]
    report = engine.build_edge_validation_report(trades, timestamp=TS)
    assert report.expectancy.value == pytest.approx((100.0 - 50.0 + 200.0) / 3)


def test_max_drawdown_and_streaks_computed_from_real_sequence():
    trades = [engine.build_trade_analytics(_shadow_position(p, "X"), [p], 24000.0, 24100.0) for p in (100.0, -50.0, -30.0, 200.0)]
    report = engine.build_edge_validation_report(trades, timestamp=TS)
    assert report.longest_losing_streak.value == 2.0
    assert report.longest_winning_streak.value == 1.0


def test_every_metric_carries_its_own_sample_size():
    trades = [engine.build_trade_analytics(_shadow_position(100.0, "X"), [100.0], 24000.0, 24100.0)]
    report = engine.build_edge_validation_report(trades, timestamp=TS)
    for metric in (report.win_rate, report.expectancy, report.profit_factor, report.average_winner,
                  report.max_drawdown, report.longest_winning_streak):
        assert metric.sample_size >= 0
        assert metric.reliability is not None


# --- Deliverable 6: counterfactual analysis, descriptive only --------------

def test_counterfactual_classifies_rejected_winner():
    decisions = [_decision(family="SHORT_DIRECTIONAL", outcome="NO_TRADE", direction="BULLISH")]
    report = engine.build_counterfactual_report(decisions, {"2026-05-25": 1.5}, timestamp=TS)
    assert report.rejected_winners == 1
    assert report.records[0].classification == taxonomy.COUNTERFACTUAL_REJECTED_WINNER


def test_counterfactual_classifies_rejected_loser():
    decisions = [_decision(family="SHORT_DIRECTIONAL", outcome="NO_TRADE", direction="BULLISH")]
    report = engine.build_counterfactual_report(decisions, {"2026-05-25": -1.5}, timestamp=TS)
    assert report.rejected_losers == 1


def test_counterfactual_excludes_approved_decisions():
    decisions = [_decision(family="LONG_DIRECTIONAL", outcome="TRADE_APPROVED", direction="BULLISH")]
    report = engine.build_counterfactual_report(decisions, {"2026-05-25": 1.5}, timestamp=TS)
    assert report.total_non_approved == 0


def test_counterfactual_unknown_for_neutral_direction():
    decisions = [_decision(family=None, outcome="NO_TRADE", direction="NEUTRAL")]
    report = engine.build_counterfactual_report(decisions, {"2026-05-25": 1.5}, timestamp=TS)
    assert report.unknown == 1


def test_counterfactual_unknown_when_no_outcome_data():
    decisions = [_decision(family="SHORT_DIRECTIONAL", outcome="NO_TRADE", direction="BULLISH")]
    report = engine.build_counterfactual_report(decisions, {}, timestamp=TS)
    assert report.unknown == 1


def test_counterfactual_never_changes_a_decision():
    d = _decision(family="SHORT_DIRECTIONAL", outcome="NO_TRADE", direction="BULLISH")
    original_outcome = d.decision_outcome
    engine.build_counterfactual_report([d], {"2026-05-25": 1.5}, timestamp=TS)
    assert d.decision_outcome == original_outcome  # immutable, untouched.


# --- Deliverable 5: root-cause breakdowns are descriptive only -------------

def test_root_cause_by_direction_groups_correctly():
    trades = [
        engine.build_trade_analytics(_shadow_position(100.0, "X"), [100.0], 24000.0, 24100.0),
        engine.build_trade_analytics(_shadow_position(-50.0, "X"), [-50.0], 24000.0, 23900.0),
    ]
    positions_by_id = {trades[0].shadow_trade_id: _shadow_position(100.0, "X"), trades[1].shadow_trade_id: _shadow_position(-50.0, "X")}
    breakdown = pa_query.root_cause_by_direction(trades, positions_by_id)
    assert "UP" in breakdown and "DOWN" in breakdown


# --- Deliverable 8: dashboard views -----------------------------------------

def test_overall_dashboard_shows_trade_counts():
    trades = [engine.build_trade_analytics(_shadow_position(100.0, "X"), [100.0], 24000.0, 24100.0)]
    report = engine.build_edge_validation_report(trades, timestamp=TS)
    view = pa_query.overall_dashboard(11, 4, 7, report)
    assert "11" in view
    assert "Win" in view and "Loss" in view


def test_reasoning_never_cites_ml_or_optimization():
    forbidden = ("tune", "optimi", "reinforce", "train the model", "machine learning")
    trades = [engine.build_trade_analytics(_shadow_position(100.0, "X"), [100.0], 24000.0, 24100.0)]
    report = engine.build_edge_validation_report(trades, timestamp=TS)
    text = " ".join(report.explanation).lower()
    for word in forbidden:
        assert word not in text


# --- Determinism / immutability --------------------------------------------

def test_determinism_identical_input_identical_id():
    trades = [engine.build_trade_analytics(_shadow_position(100.0, "X"), [100.0], 24000.0, 24100.0)]
    r1 = engine.build_edge_validation_report(trades, timestamp=TS)
    r2 = engine.build_edge_validation_report(trades, timestamp=TS)
    assert r1.assessment_id == r2.assessment_id


def test_assessment_is_immutable():
    trades = [engine.build_trade_analytics(_shadow_position(100.0, "X"), [100.0], 24000.0, 24100.0)]
    report = engine.build_edge_validation_report(trades, timestamp=TS)
    with pytest.raises(Exception):
        report.win_rate = None


def test_batch_matches_individual_calls():
    requests = [dict(position=_shadow_position(100.0, "X"), daily_marks=[100.0], spot_at_entry=24000.0, spot_at_exit=24100.0)]
    batch = build_trade_analytics_batch(requests)
    individual = engine.build_trade_analytics(**requests[0])
    assert batch[0].realised_pnl == individual.realised_pnl


# --- Journal / serialization -------------------------------------------

def test_journal_records_trade_analytics():
    j = PerformanceAnalyticsJournal()
    ta = engine.build_trade_analytics(_shadow_position(100.0, "X"), [100.0], 24000.0, 24100.0)
    j.record_trade_analytics(ta, recorded_at=TS)
    assert len(j) == 1


def test_serialization_round_trip():
    ta = engine.build_trade_analytics(_shadow_position(100.0, "X"), [100.0], 24000.0, 24100.0)
    d = pa_serialization.trade_analytics_to_dict(ta)
    assert d["shadow_trade_id"] == ta.shadow_trade_id


# --- AST isolation (house convention) --------------------------------------

def _pkg_files():
    pkg_dir = os.path.join(os.path.dirname(__file__), "..", "bujji", "msi_performance_analytics")
    return [os.path.join(pkg_dir, f) for f in os.listdir(pkg_dir) if f.endswith(".py")]


def test_ast_no_forbidden_imports():
    forbidden_modules = ("mic_v2", "bujji.production_runtime", "bujji.trading_brain", "fyers_apiv3",
                         "bujji.broker", "bujji.execution", "sklearn", "torch", "tensorflow")
    for path in _pkg_files():
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not any(name.startswith(m) for m in forbidden_modules), f"{path} imports {name}"


def test_ast_no_ml_or_optimization_vocabulary():
    forbidden_fragments = ("optimi", "reinforce", "reward", "gradientdescent", "neuralnet")
    for path in _pkg_files():
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)):
                identifier = (node.id if isinstance(node, ast.Name) else node.attr).lower().replace("_", "")
                for term in forbidden_fragments:
                    assert term not in identifier, f"{path} contains forbidden identifier fragment '{term}'"
                assert identifier != "uuid4", f"{path} calls uuid4()"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "random", f"{path} imports random"
        assert "datetime.now(" not in source, f"{path} uses wall-clock now()"
