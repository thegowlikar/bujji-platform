"""Tests for the Execution Planner — Engineering Series 37, Sprint 1."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.capital_brain.models import CapitalDecision
from bujji.trading_brain.strategy_selector.models import StrategyDecision
from bujji.trading_brain.execution_planner import config, engine, models, query, runner, serialization, taxonomy
from bujji.journal.execution_planner_journal import ExecutionPlannerJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 10, 15, 0)


def _capital(**overrides):
    base = dict(
        decision_id="CD-0000000000000001",
        capital_intent="STANDARD",
        allocation_status="APPROVED",
        allocation_reason="stub allocation reason",
        allocation_constraints=("NONE",),
        required_controls=("NONE",),
        confidence="VERY_HIGH",
        decision_trace="stub decision trace",
        risk_assessment_id="RA-0000000000000001",
        timestamp="2026-01-01T10:00:00",
        version="1.0.0",
    )
    base.update(overrides)
    return CapitalDecision(**base)


def _strategy(**overrides):
    base = dict(
        decision_id="SD-0000000000000001",
        selected_strategy="PREMIUM_VWAP_STRADDLE",
        selection_status="SELECTED",
        selection_confidence="VERY_HIGH",
        selection_reason="stub selection reason",
        supporting_conditions=("stub supporting condition",),
        rejecting_conditions=(),
        alternative_candidates=(),
        all_evaluations=(),
        decision_trace="stub decision trace",
        market_state_assessment_id="MSA-0000000000000001",
        timestamp="2026-01-01T09:30:00",
        version="1.0.0",
    )
    base.update(overrides)
    return StrategyDecision(**base)


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_status_values(self):
        assert taxonomy.ALL_STATUSES == ("UNKNOWN", "NOT_PLANNED", "PLANNED", "BLOCKED")

    def test_execution_intent_values(self):
        assert taxonomy.ALL_EXECUTION_INTENTS == (
            "NONE", "PREPARE", "ENTER", "MONITOR", "EXIT", "UNKNOWN",
        )

    def test_execution_constraint_values(self):
        assert taxonomy.ALL_EXECUTION_CONSTRAINTS == (
            "NONE", "WAIT_FOR_CONFIRMATION", "REDUCE_SIZE", "MANUAL_APPROVAL", "FOLLOW_RISK_CONTROLS",
        )

    def test_execution_step_type_values(self):
        assert taxonomy.ALL_EXECUTION_STEP_TYPES == (
            "VALIDATE", "PREPARE", "WAIT", "ENTER", "MONITOR", "EXIT",
        )

    def test_every_status_has_description(self):
        for s in taxonomy.ALL_STATUSES:
            assert s in taxonomy.STATUS_DESCRIPTIONS and taxonomy.STATUS_DESCRIPTIONS[s]

    def test_every_execution_intent_has_description(self):
        for e in taxonomy.ALL_EXECUTION_INTENTS:
            assert e in taxonomy.EXECUTION_INTENT_DESCRIPTIONS and taxonomy.EXECUTION_INTENT_DESCRIPTIONS[e]

    def test_every_constraint_has_description(self):
        for c in taxonomy.ALL_EXECUTION_CONSTRAINTS:
            assert c in taxonomy.EXECUTION_CONSTRAINT_DESCRIPTIONS and taxonomy.EXECUTION_CONSTRAINT_DESCRIPTIONS[c]

    def test_every_step_type_has_description(self):
        for s in taxonomy.ALL_EXECUTION_STEP_TYPES:
            assert s in taxonomy.EXECUTION_STEP_TYPE_DESCRIPTIONS and taxonomy.EXECUTION_STEP_TYPE_DESCRIPTIONS[s]


# ---------------------------------------------------------------------------
# Missing input
# ---------------------------------------------------------------------------

class TestMissingInput:
    def test_none_capital_decision_yields_unknown(self):
        result = engine.plan(None, _strategy(), clock=FIXED_CLOCK)
        assert result.status == "UNKNOWN"
        assert result.execution_intent == "UNKNOWN"
        assert result.confidence == "UNKNOWN"
        assert result.capital_decision_id is None

    def test_none_capital_decision_never_fabricates_plan(self):
        result = engine.plan(None, None, clock=FIXED_CLOCK)
        assert result.execution_steps == ()
        assert result.execution_constraints == ("NONE",)


# ---------------------------------------------------------------------------
# Denied allocation
# ---------------------------------------------------------------------------

class TestDeniedAllocation:
    def test_denied_yields_not_planned_none(self):
        result = engine.plan(_capital(allocation_status="DENIED", capital_intent="NONE"), _strategy(), clock=FIXED_CLOCK)
        assert result.status == "NOT_PLANNED"
        assert result.execution_intent == "NONE"
        assert result.execution_steps == ()

    def test_denied_preserves_confidence(self):
        result = engine.plan(_capital(allocation_status="DENIED", confidence="MODERATE"), _strategy(), clock=FIXED_CLOCK)
        assert result.confidence == "MODERATE"

    def test_denied_never_checks_strategy(self):
        # Even with a missing strategy, DENIED short-circuits before
        # any strategy-related check.
        result = engine.plan(_capital(allocation_status="DENIED"), None, clock=FIXED_CLOCK)
        assert result.status == "NOT_PLANNED"


# ---------------------------------------------------------------------------
# Limited allocation
# ---------------------------------------------------------------------------

class TestLimitedAllocation:
    def test_limited_yields_planned_prepare(self):
        result = engine.plan(_capital(allocation_status="LIMITED", capital_intent="REDUCED"), _strategy(), clock=FIXED_CLOCK)
        assert result.status == "PLANNED"
        assert result.execution_intent == "PREPARE"
        assert "FOLLOW_RISK_CONTROLS" in result.execution_constraints
        assert "REDUCE_SIZE" in result.execution_constraints

    def test_limited_downgrades_confidence(self):
        result = engine.plan(_capital(allocation_status="LIMITED", confidence="HIGH"), _strategy(), clock=FIXED_CLOCK)
        assert result.confidence == "MODERATE"

    def test_limited_produces_five_steps(self):
        result = engine.plan(_capital(allocation_status="LIMITED"), _strategy(), clock=FIXED_CLOCK)
        assert len(result.execution_steps) == 5


# ---------------------------------------------------------------------------
# Approved allocation
# ---------------------------------------------------------------------------

class TestApprovedAllocation:
    def test_approved_yields_planned_prepare(self):
        result = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        assert result.status == "PLANNED"
        assert result.execution_intent == "PREPARE"
        assert result.execution_constraints == ("NONE",)

    def test_approved_preserves_confidence(self):
        result = engine.plan(_capital(confidence="HIGH"), _strategy(), clock=FIXED_CLOCK)
        assert result.confidence == "HIGH"

    def test_approved_step_sequence(self):
        result = engine.plan(_capital(), _strategy(selected_strategy="IRON_FLY"), clock=FIXED_CLOCK)
        step_types = [s.step_type for s in result.execution_steps]
        assert step_types == ["VALIDATE", "VALIDATE", "VALIDATE", "PREPARE", "WAIT"]
        assert "IRON_FLY" in result.execution_steps[2].description

    def test_never_produces_enter_monitor_exit_intent(self):
        for allocation_status in ("APPROVED", "LIMITED", "DENIED", "UNKNOWN"):
            result = engine.plan(_capital(allocation_status=allocation_status), _strategy(), clock=FIXED_CLOCK)
            assert result.execution_intent not in ("ENTER", "MONITOR", "EXIT")


# ---------------------------------------------------------------------------
# Blocked planning
# ---------------------------------------------------------------------------

class TestBlockedPlanning:
    def test_missing_strategy_with_approved_capital_is_blocked(self):
        result = engine.plan(_capital(), None, clock=FIXED_CLOCK)
        assert result.status == "BLOCKED"
        assert "MANUAL_APPROVAL" in result.execution_constraints

    def test_unresolved_strategy_selection_is_blocked(self):
        strategy = _strategy(selected_strategy=None, selection_status="NO_STRATEGY")
        result = engine.plan(_capital(), strategy, clock=FIXED_CLOCK)
        assert result.status == "BLOCKED"

    def test_blocked_downgrades_confidence(self):
        result = engine.plan(_capital(confidence="HIGH"), None, clock=FIXED_CLOCK)
        assert result.confidence == "MODERATE"

    def test_blocked_produces_no_steps(self):
        result = engine.plan(_capital(), None, clock=FIXED_CLOCK)
        assert result.execution_steps == ()

    def test_denied_capital_with_missing_strategy_is_not_planned_not_blocked(self):
        # DENIED short-circuits before the strategy check.
        result = engine.plan(_capital(allocation_status="DENIED"), None, clock=FIXED_CLOCK)
        assert result.status == "NOT_PLANNED"


# ---------------------------------------------------------------------------
# Planning trace
# ---------------------------------------------------------------------------

class TestPlanningTrace:
    def test_trace_mentions_capital_intent(self):
        result = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        assert result.capital_intent in result.planning_trace

    def test_trace_mentions_strategy(self):
        result = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        assert "PREMIUM_VWAP_STRADDLE" in result.planning_trace

    def test_trace_mentions_status(self):
        result = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        assert result.status in result.planning_trace


# ---------------------------------------------------------------------------
# Determinism / replay
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_plan_id(self):
        p1 = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        p2 = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        assert p1.plan_id == p2.plan_id
        assert p1 == p2

    def test_byte_identical_across_repeated_runs(self):
        results = [engine.plan(_capital(allocation_status="LIMITED"), _strategy(), clock=FIXED_CLOCK) for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_different_inputs_different_plan_id(self):
        p1 = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        p2 = engine.plan(_capital(allocation_status="DENIED"), _strategy(), clock=FIXED_CLOCK)
        assert p1.plan_id != p2.plan_id

    def test_plan_id_uses_md5_prefix_not_uuid(self):
        result = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        assert result.plan_id.startswith("EP-")
        assert len(result.plan_id) == len("EP-") + 16

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        result = engine.plan(_capital(), _strategy())
        after = datetime.now()
        parsed = datetime.fromisoformat(result.timestamp)
        assert before <= parsed <= after

    def test_no_randomness_module_used(self):
        source = inspect.getsource(engine)
        assert "import random" not in source
        assert "random." not in source

    def test_no_ml_library_imports(self):
        source = inspect.getsource(engine)
        for forbidden in ("sklearn", "torch", "tensorflow", "numpy"):
            assert forbidden not in source


# ---------------------------------------------------------------------------
# Frozen dataclasses / no mutation
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_plan_is_frozen(self):
        result = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            result.status = "HACKED"

    def test_step_is_frozen(self):
        result = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            result.execution_steps[0].description = "HACKED"

    def test_engine_does_not_mutate_inputs(self):
        c = _capital()
        s = _strategy()
        c_copy = CapitalDecision(**{f.name: getattr(c, f.name) for f in c.__dataclass_fields__.values()})
        s_copy = StrategyDecision(**{f.name: getattr(s, f.name) for f in s.__dataclass_fields__.values()})
        engine.plan(c, s, clock=FIXED_CLOCK)
        assert c == c_copy
        assert s == s_copy


# ---------------------------------------------------------------------------
# Serialization round trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        result = engine.plan(_capital(allocation_status="LIMITED"), _strategy(), clock=FIXED_CLOCK)
        d = serialization.plan_to_dict(result)
        back = serialization.plan_from_dict(d)
        assert back == result

    def test_round_trip_is_json_safe(self):
        import json

        result = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        d = serialization.plan_to_dict(result)
        text = json.dumps(d)
        back = serialization.plan_from_dict(json.loads(text))
        assert back == result

    def test_round_trip_with_no_steps(self):
        result = engine.plan(_capital(allocation_status="DENIED"), _strategy(), clock=FIXED_CLOCK)
        d = serialization.plan_to_dict(result)
        assert d["execution_steps"] == []
        back = serialization.plan_from_dict(d)
        assert back.execution_steps == ()


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = ExecutionPlannerJournal(tmp_path / "ep.jsonl")
        result = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        j.record(result)
        assert j.read_all() == [result]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = ExecutionPlannerJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = ExecutionPlannerJournal(tmp_path / "j.jsonl")
        p1 = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        p2 = engine.plan(_capital(allocation_status="DENIED"), _strategy(), clock=FIXED_CLOCK)
        j.record_many([p1, p2])
        records = j.read_all()
        assert [r.plan_id for r in records] == [p1.plan_id, p2.plan_id]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(ExecutionPlannerJournal)
        tree = ast.parse(source)
        open_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open"
        ]
        assert open_calls
        for call in open_calls:
            modes = [a.value for a in call.args if isinstance(a, ast.Constant)]
            modes += [
                kw.value.value
                for kw in call.keywords
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant)
            ]
            assert all(m in ("a", "r") for m in modes if isinstance(m, str))


# ---------------------------------------------------------------------------
# runner.run_planning composition
# ---------------------------------------------------------------------------

class TestRunner:
    def test_run_without_journal(self):
        result = runner.run_planning(_capital(), _strategy(), clock=FIXED_CLOCK)
        assert isinstance(result, models.ExecutionPlan)

    def test_run_journals_when_given_one(self, tmp_path):
        j = ExecutionPlannerJournal(tmp_path / "run.jsonl")
        result = runner.run_planning(_capital(), _strategy(), clock=FIXED_CLOCK, journal=j)
        assert j.read_all() == [result]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestExecutionPlanIndex:
    def test_ingest_and_latest(self):
        idx = query.ExecutionPlanIndex()
        p1 = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        idx.ingest(p1)
        assert idx.latest() == p1

    def test_latest_none_when_empty(self):
        assert query.ExecutionPlanIndex().latest() is None

    def test_history_preserves_order(self):
        idx = query.ExecutionPlanIndex()
        p1 = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        p2 = engine.plan(_capital(allocation_status="DENIED"), _strategy(), clock=FIXED_CLOCK)
        idx.ingest(p1)
        idx.ingest(p2)
        assert idx.history() == [p1, p2]

    def test_find_by_id(self):
        idx = query.ExecutionPlanIndex()
        p1 = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        idx.ingest(p1)
        assert idx.find_by_id(p1.plan_id) == p1
        assert idx.find_by_id("nope") is None

    def test_find_by_status(self):
        idx = query.ExecutionPlanIndex()
        p1 = engine.plan(_capital(allocation_status="DENIED"), _strategy(), clock=FIXED_CLOCK)
        idx.ingest(p1)
        assert idx.find_by_status("NOT_PLANNED") == [p1]

    def test_find_by_strategy(self):
        idx = query.ExecutionPlanIndex()
        p1 = engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK)
        idx.ingest(p1)
        assert idx.find_by_strategy("PREMIUM_VWAP_STRADDLE") == [p1]

    def test_summary_counts_by_status(self):
        idx = query.ExecutionPlanIndex()
        idx.ingest(engine.plan(_capital(), _strategy(), clock=FIXED_CLOCK))
        idx.ingest(engine.plan(_capital(allocation_status="DENIED"), _strategy(), clock=FIXED_CLOCK))
        assert idx.summary() == {"PLANNED": 1, "NOT_PLANNED": 1}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_version_matches_package_version(self):
        cfg = config.ExecutionPlannerConfig()
        assert cfg.version == taxonomy.EXECUTION_PLANNER_VERSION

    def test_config_is_frozen(self):
        cfg = config.ExecutionPlannerConfig()
        with pytest.raises(Exception):
            cfg.version = "9.9.9"


# ---------------------------------------------------------------------------
# Isolation firewall
# ---------------------------------------------------------------------------

FORBIDDEN_BUJJI_SUBPACKAGES = {
    "broker", "core", "execution", "trade", "intelligence", "market", "tick",
}


def _module_source_files():
    base = Path(engine.__file__).parent
    return list(base.glob("*.py"))


class TestIsolation:
    def test_no_mic_v2_import_anywhere(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "mic_v2" not in alias.name
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "mic_v2" not in node.module

    def test_no_market_state_or_risk_brain_or_evidence_interpreter_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for forbidden in ("market_state", "risk_brain", "evidence_interpreter"):
                        assert forbidden not in node.module

    def test_no_forbidden_bujji_subpackage_imports(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    parts = node.module.split(".")
                    if "bujji" in parts:
                        idx = parts.index("bujji")
                        rest = parts[idx + 1:]
                        assert not (rest and rest[0] in FORBIDDEN_BUJJI_SUBPACKAGES), (
                            f"{path.name} imports forbidden bujji.{rest[0]}"
                        )

    def test_no_broker_or_order_or_feed_or_replay_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    lowered = node.module.lower()
                    for forbidden in ("broker", "order", "feed", "replay", "fyers", "zerodha"):
                        assert forbidden not in lowered

    def test_no_order_or_payload_or_strike_or_quantity_fields(self):
        plan_fields = {f.name for f in models.ExecutionPlan.__dataclass_fields__.values()}
        step_fields = {f.name for f in models.ExecutionStep.__dataclass_fields__.values()}
        forbidden = {
            "order_id", "payload", "strike", "expiry", "quantity", "lots",
            "fill_price", "account_balance", "margin",
        }
        assert not (plan_fields & forbidden)
        assert not (step_fields & forbidden)

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_no_execution_capability_in_source(self):
        for path in _module_source_files():
            source = path.read_text()
            for forbidden in ("place_order(", "def execute(", "def calculate_quantity", "def generate_payload"):
                assert forbidden not in source

    def test_no_broker_connection_terms_in_executable_code(self):
        tree = ast.parse(inspect.getsource(engine))
        tree.body = [
            node
            for node in tree.body
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        ]
        code_only = ast.unparse(tree).lower()
        for forbidden in ("fyers", "zerodha", "place_order", "api_key"):
            assert forbidden not in code_only
