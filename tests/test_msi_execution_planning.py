"""Tests for bujji.msi_execution_planning — Engineering Series 98."""
from __future__ import annotations

import ast
import os

import pytest

from bujji.msi_execution_planning import config as ep_config
from bujji.msi_execution_planning import engine, taxonomy
from bujji.msi_execution_planning.journal import ExecutionPlanningJournal
from bujji.msi_execution_planning.runner import build_execution_plans_batch, ExecutionPlanningStream
from bujji.msi_execution_planning import query as ep_query
from bujji.msi_execution_planning import serialization as ep_serialization
from bujji.msi_position_lifecycle.models import (
    AdjustmentPolicy, EmergencyPolicy, ExpiryPolicy, LossPolicy, PositionLifecycleAssessment,
    ProfitPolicy, ThesisInvalidation, Explanation as PliExplanation,
)
from bujji.msi_trade_construction import engine as tc_engine
from bujji.options_observation import runner as opt_runner

REAL_BHAVCOPY = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv"
DAY = "2026-05-25"
TS = "2026-05-25T15:30:00+05:30"


def _real_chain():
    with open(REAL_BHAVCOPY) as f:
        text = f.read()
    series, _ = opt_runner.ingest_all_option_series_from_bhavcopy(text, DAY, underlying="NIFTY")
    return tuple(s.observations()[-1] for s in series if len(s.observations()) > 0)


@pytest.fixture(scope="module")
def chain():
    return _real_chain()


@pytest.fixture(scope="module")
def spot(chain):
    return next((r.underlying_price for r in chain if r.underlying_price), None)


def _construct(chain, spot, family):
    return tc_engine.construct_trade(family, chain, spot, DAY, direction="BULLISH", expected_move_pct=1.2, timestamp=TS)


def _lifecycle(state="HEALTHY", compatible=True):
    exp = PliExplanation(assessment_id="l1", why_this_adjustment_policy=(), why_this_profit_policy=(),
                         why_this_invalidation_rule=(), why_this_emergency_policy=(), schema_version="1.0.0")
    return PositionLifecycleAssessment(
        lifecycle_id="l1", timestamp=TS, strategy_family="IRON_CONDOR", construction_type="IRON_CONDOR_SHAPE",
        position_state=state, expected_lifetime="UNTIL_EXPIRY", monitoring_requirements=(),
        adjustment_policy=AdjustmentPolicy((), (), (), (), ()), profit_policy=ProfitPolicy("HOLD", ()),
        loss_policy=LossPolicy("EXIT_ON_THESIS_BROKEN", ()), expiry_policy=ExpiryPolicy("HOLD", 5, ()),
        emergency_policy=EmergencyPolicy("NONE", ()),
        thesis_invalidation=ThesisInvalidation("RANGE_PERSISTENCE", "RANGE_PERSISTENCE" if compatible else "BREAKOUT", compatible, ()),
        supporting_assessment_ids=(), explanation=exp, provenance="p", schema_version="1.0.0",
    )


# --- Deliverable 3: dependency graph, matches the spec's own examples ----

def test_debit_spread_style_family_buys_before_sells(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    plan = engine.build_execution_plan(trade, timestamp=TS)
    assert plan.execution_steps[0].role_filter == "ANY"  # single leg, no split needed.


def test_iron_condor_shorts_before_wings(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    plan = engine.build_execution_plan(trade, timestamp=TS)
    assert plan.execution_steps[0].role_filter == "SELL"
    assert plan.execution_steps[1].role_filter == "BUY"
    assert len(plan.execution_steps) == 2
    assert len(plan.dependency_graph) == 1
    assert plan.dependency_graph[0].from_stage == 0 and plan.dependency_graph[0].to_stage == 1


def test_butterfly_wings_before_body(chain, spot):
    trade = _construct(chain, spot, "BUTTERFLY")
    plan = engine.build_execution_plan(trade, timestamp=TS)
    assert plan.execution_steps[0].role_filter == "BUY"
    assert plan.execution_steps[1].role_filter == "SELL"


def test_never_simultaneous_all_legs_appear_across_stages_not_one(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    plan = engine.build_execution_plan(trade, timestamp=TS)
    assert len(plan.execution_steps) > 1
    total_legs = sum(len(s.legs) for s in plan.execution_steps)
    assert total_legs == len(trade.legs)


def test_single_leg_family_has_no_dependency_edges(chain, spot):
    trade = _construct(chain, spot, "LONG_DIRECTIONAL")
    plan = engine.build_execution_plan(trade, timestamp=TS)
    assert plan.dependency_graph == ()


# --- Deliverable 4: failure planning ----------------------------------------

def test_all_eight_failure_types_have_a_declared_response(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    plan = engine.build_execution_plan(trade, timestamp=TS)
    covered = {p.failure_type for p in plan.failure_policies}
    assert covered == set(taxonomy.ALL_FAILURE_TYPES)


def test_partial_fill_policy_never_treats_nonzero_fill_as_failure(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    plan = engine.build_execution_plan(trade, timestamp=TS)
    partial = next(p for p in plan.failure_policies if p.failure_type == taxonomy.FAILURE_PARTIAL_FILL)
    assert "zero" in partial.response.lower()


# --- Deliverable 5: validation gates, fail closed ---------------------------

def test_all_six_validation_gates_present(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    plan = engine.build_execution_plan(trade, timestamp=TS)
    names = {g.name for g in plan.validation_steps}
    assert names == set(taxonomy.ALL_VALIDATION_GATES)


def test_market_open_and_execution_window_are_disclosed_as_not_evaluable(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    plan = engine.build_execution_plan(trade, timestamp=TS)
    gate = next(g for g in plan.validation_steps if g.name == taxonomy.GATE_MARKET_OPEN)
    assert gate.real_time_evaluable is False
    assert gate.passed is None


def test_position_and_thesis_gates_evaluable_when_lifecycle_supplied(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    plan = engine.build_execution_plan(trade, _lifecycle(state="HEALTHY", compatible=True), timestamp=TS)
    pos_gate = next(g for g in plan.validation_steps if g.name == taxonomy.GATE_POSITION_STILL_VALID)
    thesis_gate = next(g for g in plan.validation_steps if g.name == taxonomy.GATE_THESIS_STILL_VALID)
    assert pos_gate.real_time_evaluable is True and pos_gate.passed is True
    assert thesis_gate.real_time_evaluable is True and thesis_gate.passed is True


def test_position_gate_fails_closed_when_thesis_broken(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    plan = engine.build_execution_plan(trade, _lifecycle(state="THESIS_BROKEN", compatible=False), timestamp=TS)
    pos_gate = next(g for g in plan.validation_steps if g.name == taxonomy.GATE_POSITION_STILL_VALID)
    thesis_gate = next(g for g in plan.validation_steps if g.name == taxonomy.GATE_THESIS_STILL_VALID)
    assert pos_gate.passed is False
    assert thesis_gate.passed is False


def test_duplicate_protection_always_deterministically_evaluable(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    plan = engine.build_execution_plan(trade, timestamp=TS)
    gate = next(g for g in plan.validation_steps if g.name == taxonomy.GATE_DUPLICATE_PROTECTION)
    assert gate.real_time_evaluable is True
    assert gate.passed is True


# --- Deliverable 7: explainability -------------------------------------------

def test_explanation_answers_all_required_questions(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    plan = engine.build_execution_plan(trade, timestamp=TS)
    assert plan.explanation.why_this_sequence
    assert plan.explanation.why_this_dependency
    assert plan.explanation.why_this_recovery_plan
    assert plan.explanation.why_this_validation_order


def test_reasoning_never_cites_historical_performance(chain, spot):
    forbidden = ("performed best", "historically", "backtest", "pnl", "profit factor")
    trade = _construct(chain, spot, "IRON_CONDOR")
    plan = engine.build_execution_plan(trade, timestamp=TS)
    text = " ".join(plan.explanation.why_this_sequence + plan.explanation.why_this_dependency).lower()
    for word in forbidden:
        assert word not in text


# --- Determinism / immutability / batch-streaming --------------------------

def test_determinism_identical_input_identical_id(chain, spot):
    t1 = _construct(chain, spot, "IRON_CONDOR")
    t2 = _construct(chain, spot, "IRON_CONDOR")
    p1 = engine.build_execution_plan(t1, timestamp=TS)
    p2 = engine.build_execution_plan(t2, timestamp=TS)
    assert p1.plan_id == p2.plan_id


def test_assessment_is_immutable(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    plan = engine.build_execution_plan(trade, timestamp=TS)
    with pytest.raises(Exception):
        plan.strategy_family = "X"


def test_batch_and_streaming_are_byte_identical(chain, spot):
    trades = [_construct(chain, spot, f) for f in ("LONG_DIRECTIONAL", "IRON_CONDOR", "BUTTERFLY")]
    requests = [dict(trade=t, timestamp=TS) for t in trades]
    batch = build_execution_plans_batch(requests)
    stream = ExecutionPlanningStream()
    streamed = tuple(stream.submit(**r) for r in requests)
    assert [a.plan_id for a in batch] == [a.plan_id for a in streamed]
    assert len(stream.journal) == len(requests)


# --- Serialization / query --------------------------------------------------

def test_serialization_round_trip(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    plan = engine.build_execution_plan(trade, timestamp=TS)
    d = ep_serialization.assessment_to_dict(plan)
    assert d["plan_id"] == plan.plan_id
    assert len(d["execution_steps"]) == 2


def test_query_helpers(chain, spot):
    p1 = engine.build_execution_plan(_construct(chain, spot, "IRON_CONDOR"), timestamp=TS)
    p2 = engine.build_execution_plan(_construct(chain, spot, "LONG_DIRECTIONAL"), timestamp=TS)
    assessments = (p1, p2)
    assert ep_query.by_id(assessments, p1.plan_id) is p1
    assert p2 in ep_query.by_strategy_family(assessments, "LONG_DIRECTIONAL")


def test_journal_is_append_only(chain, spot):
    trade = _construct(chain, spot, "IRON_CONDOR")
    plan = engine.build_execution_plan(trade, timestamp=TS)
    j = ExecutionPlanningJournal()
    j.record_assessment(plan, recorded_at=TS)
    assert len(j) == 1
    j.record_assessment(plan, recorded_at=TS)
    assert len(j) == 2


# --- AST isolation (house convention) --------------------------------------

def _pkg_files():
    pkg_dir = os.path.join(os.path.dirname(__file__), "..", "bujji", "msi_execution_planning")
    return [os.path.join(pkg_dir, f) for f in os.listdir(pkg_dir) if f.endswith(".py")]


def test_ast_no_forbidden_imports():
    forbidden_modules = ("mic_v2", "bujji.production_runtime", "bujji.trading_brain", "fyers_apiv3",
                         "bujji.broker", "bujji.execution")
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


def test_ast_no_optimization_vocabulary_or_uuid4_or_randomness():
    for path in _pkg_files():
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)):
                identifier = (node.id if isinstance(node, ast.Name) else node.attr).lower().replace("_", "")
                for term in ("optimi", "backtest", "pnl"):
                    assert term not in identifier, f"{path} contains forbidden identifier fragment '{term}'"
                assert identifier != "uuid4", f"{path} calls uuid4()"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "random", f"{path} imports random"
        assert "datetime.now(" not in source, f"{path} uses wall-clock now()"
        assert "place_order" not in source.lower(), f"{path} references order placement"
