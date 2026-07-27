"""Tests for the Execution Engine — Engineering Series 39, Sprint 1."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.execution_planner.models import ExecutionPlan
from bujji.trading_brain.execution_engine import config, engine, models, query, runner, serialization, taxonomy
from bujji.journal.execution_engine_journal import ExecutionEngineJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 11, 15, 0)


def _plan(**overrides):
    base = dict(
        plan_id="EP-0000000000000001",
        status="PLANNED",
        execution_intent="PREPARE",
        strategy_id="PREMIUM_VWAP_STRADDLE",
        capital_intent="STANDARD",
        required_controls=("NONE",),
        execution_constraints=("NONE",),
        execution_steps=(),
        confidence="VERY_HIGH",
        planning_trace="stub planning trace",
        capital_decision_id="CD-0000000000000001",
        strategy_decision_id="SD-0000000000000001",
        timestamp="2026-01-01T10:15:00",
        version="1.0.0",
    )
    base.update(overrides)
    return ExecutionPlan(**base)


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_status_values(self):
        assert taxonomy.ALL_STATUSES == ("UNKNOWN", "BLOCKED", "READY_FOR_ADAPTER")

    def test_abstract_action_values(self):
        assert taxonomy.ALL_ABSTRACT_ACTIONS == (
            "VALIDATE_PLAN", "VALIDATE_CONTROLS", "AUTHORIZE_EXECUTION",
            "WAIT_FOR_ADAPTER", "EXECUTE", "COMPLETE", "BLOCK",
        )

    def test_blocking_condition_values(self):
        assert taxonomy.ALL_BLOCKING_CONDITIONS == (
            "MISSING_PLAN", "FAILED_VALIDATION", "CONTROL_FAILURE", "UNKNOWN_INTENT",
        )

    def test_every_status_has_description(self):
        for s in taxonomy.ALL_STATUSES:
            assert s in taxonomy.STATUS_DESCRIPTIONS and taxonomy.STATUS_DESCRIPTIONS[s]

    def test_every_action_has_description(self):
        for a in taxonomy.ALL_ABSTRACT_ACTIONS:
            assert a in taxonomy.ABSTRACT_ACTION_DESCRIPTIONS and taxonomy.ABSTRACT_ACTION_DESCRIPTIONS[a]

    def test_every_blocking_condition_has_description(self):
        for b in taxonomy.ALL_BLOCKING_CONDITIONS:
            assert b in taxonomy.BLOCKING_CONDITION_DESCRIPTIONS and taxonomy.BLOCKING_CONDITION_DESCRIPTIONS[b]


# ---------------------------------------------------------------------------
# Missing plan
# ---------------------------------------------------------------------------

class TestMissingPlan:
    def test_none_plan_yields_unknown(self):
        result = engine.orchestrate(None, clock=FIXED_CLOCK)
        assert result.status == "UNKNOWN"
        assert result.abstract_actions == ()
        assert result.blocking_conditions == ("MISSING_PLAN",)
        assert result.confidence == "UNKNOWN"
        assert result.plan_id is None

    def test_plan_status_unknown_yields_unknown(self):
        result = engine.orchestrate(_plan(status="UNKNOWN", confidence="UNKNOWN"), clock=FIXED_CLOCK)
        assert result.status == "UNKNOWN"
        assert result.blocking_conditions == ("MISSING_PLAN",)


# ---------------------------------------------------------------------------
# Blocked plan
# ---------------------------------------------------------------------------

class TestBlockedPlan:
    def test_not_planned_yields_blocked(self):
        result = engine.orchestrate(_plan(status="NOT_PLANNED", execution_intent="NONE"), clock=FIXED_CLOCK)
        assert result.status == "BLOCKED"
        assert result.abstract_actions == ("BLOCK",)
        assert result.blocking_conditions == ("UNKNOWN_INTENT",)

    def test_blocked_upstream_yields_blocked(self):
        result = engine.orchestrate(_plan(status="BLOCKED", strategy_id=None, execution_intent="UNKNOWN"), clock=FIXED_CLOCK)
        assert result.status == "BLOCKED"
        assert result.abstract_actions == ("BLOCK",)
        assert result.blocking_conditions == ("FAILED_VALIDATION",)

    def test_defensive_planned_without_strategy_is_blocked(self):
        result = engine.orchestrate(_plan(status="PLANNED", strategy_id=None), clock=FIXED_CLOCK)
        assert result.status == "BLOCKED"
        assert "VALIDATE_PLAN" in result.abstract_actions
        assert "BLOCK" in result.abstract_actions
        assert result.blocking_conditions == ("CONTROL_FAILURE",) or result.blocking_conditions == ("FAILED_VALIDATION",)

    def test_blocked_preserves_confidence_for_pure_pass_through_rules(self):
        result = engine.orchestrate(_plan(status="NOT_PLANNED", confidence="LOW", execution_intent="NONE"), clock=FIXED_CLOCK)
        assert result.confidence == "LOW"


# ---------------------------------------------------------------------------
# Planned execution
# ---------------------------------------------------------------------------

class TestPlannedExecution:
    def test_planned_yields_ready_for_adapter(self):
        result = engine.orchestrate(_plan(), clock=FIXED_CLOCK)
        assert result.status == "READY_FOR_ADAPTER"
        assert result.abstract_actions == (
            "VALIDATE_PLAN", "VALIDATE_CONTROLS", "AUTHORIZE_EXECUTION", "WAIT_FOR_ADAPTER",
        )
        assert result.blocking_conditions == ()

    def test_planned_with_no_constraint_preserves_confidence(self):
        result = engine.orchestrate(_plan(confidence="HIGH"), clock=FIXED_CLOCK)
        assert result.confidence == "HIGH"

    def test_planned_with_constraint_downgrades_confidence(self):
        result = engine.orchestrate(
            _plan(confidence="HIGH", execution_constraints=("FOLLOW_RISK_CONTROLS", "REDUCE_SIZE")),
            clock=FIXED_CLOCK,
        )
        assert result.confidence == "MODERATE"

    def test_required_controls_passed_through_verbatim(self):
        result = engine.orchestrate(_plan(required_controls=("FOLLOW_LIMITS",)), clock=FIXED_CLOCK)
        assert result.required_controls == ("FOLLOW_LIMITS",)

    def test_execution_intent_passed_through_verbatim(self):
        result = engine.orchestrate(_plan(execution_intent="PREPARE"), clock=FIXED_CLOCK)
        assert result.execution_intent == "PREPARE"

    def test_never_produces_execute_or_complete(self):
        result = engine.orchestrate(_plan(), clock=FIXED_CLOCK)
        assert "EXECUTE" not in result.abstract_actions
        assert "COMPLETE" not in result.abstract_actions


# ---------------------------------------------------------------------------
# Deterministic action generation / replay
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_instruction_set_id(self):
        p = _plan()
        r1 = engine.orchestrate(p, clock=FIXED_CLOCK)
        r2 = engine.orchestrate(p, clock=FIXED_CLOCK)
        assert r1.instruction_set_id == r2.instruction_set_id
        assert r1 == r2

    def test_byte_identical_across_repeated_runs(self):
        p = _plan(status="NOT_PLANNED", execution_intent="NONE")
        results = [engine.orchestrate(p, clock=FIXED_CLOCK) for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_different_input_different_instruction_set_id(self):
        r1 = engine.orchestrate(_plan(), clock=FIXED_CLOCK)
        r2 = engine.orchestrate(_plan(status="BLOCKED", strategy_id=None), clock=FIXED_CLOCK)
        assert r1.instruction_set_id != r2.instruction_set_id

    def test_instruction_set_id_uses_md5_prefix_not_uuid(self):
        result = engine.orchestrate(_plan(), clock=FIXED_CLOCK)
        assert result.instruction_set_id.startswith("EIS-")
        assert len(result.instruction_set_id) == len("EIS-") + 16

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        result = engine.orchestrate(_plan())
        after = datetime.now()
        parsed = datetime.fromisoformat(result.timestamp)
        assert before <= parsed <= after

    def test_no_randomness_module_used(self):
        source = inspect.getsource(engine)
        assert "import random" not in source
        assert "random." not in source


# ---------------------------------------------------------------------------
# Frozen dataclasses / no mutation
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_instruction_set_is_frozen(self):
        result = engine.orchestrate(_plan(), clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            result.status = "HACKED"

    def test_engine_does_not_mutate_input(self):
        p = _plan()
        p_copy = ExecutionPlan(**{f.name: getattr(p, f.name) for f in p.__dataclass_fields__.values()})
        engine.orchestrate(p, clock=FIXED_CLOCK)
        assert p == p_copy


# ---------------------------------------------------------------------------
# Serialization round trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        result = engine.orchestrate(_plan(), clock=FIXED_CLOCK)
        d = serialization.instruction_set_to_dict(result)
        back = serialization.instruction_set_from_dict(d)
        assert back == result

    def test_round_trip_is_json_safe(self):
        import json

        result = engine.orchestrate(_plan(status="NOT_PLANNED", execution_intent="NONE"), clock=FIXED_CLOCK)
        d = serialization.instruction_set_to_dict(result)
        text = json.dumps(d)
        back = serialization.instruction_set_from_dict(json.loads(text))
        assert back == result

    def test_round_trip_with_none_plan_id(self):
        result = engine.orchestrate(None, clock=FIXED_CLOCK)
        d = serialization.instruction_set_to_dict(result)
        assert d["plan_id"] is None
        back = serialization.instruction_set_from_dict(d)
        assert back.plan_id is None


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = ExecutionEngineJournal(tmp_path / "ee.jsonl")
        result = engine.orchestrate(_plan(), clock=FIXED_CLOCK)
        j.record(result)
        assert j.read_all() == [result]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = ExecutionEngineJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = ExecutionEngineJournal(tmp_path / "j.jsonl")
        r1 = engine.orchestrate(_plan(), clock=FIXED_CLOCK)
        r2 = engine.orchestrate(_plan(status="NOT_PLANNED", execution_intent="NONE"), clock=FIXED_CLOCK)
        j.record_many([r1, r2])
        records = j.read_all()
        assert [r.instruction_set_id for r in records] == [r1.instruction_set_id, r2.instruction_set_id]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(ExecutionEngineJournal)
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
# runner.run_orchestration composition
# ---------------------------------------------------------------------------

class TestRunner:
    def test_run_without_journal(self):
        result = runner.run_orchestration(_plan(), clock=FIXED_CLOCK)
        assert isinstance(result, models.ExecutionInstructionSet)

    def test_run_journals_when_given_one(self, tmp_path):
        j = ExecutionEngineJournal(tmp_path / "run.jsonl")
        result = runner.run_orchestration(_plan(), clock=FIXED_CLOCK, journal=j)
        assert j.read_all() == [result]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestExecutionInstructionSetIndex:
    def test_ingest_and_latest(self):
        idx = query.ExecutionInstructionSetIndex()
        r1 = engine.orchestrate(_plan(), clock=FIXED_CLOCK)
        idx.ingest(r1)
        assert idx.latest() == r1

    def test_latest_none_when_empty(self):
        assert query.ExecutionInstructionSetIndex().latest() is None

    def test_history_preserves_order(self):
        idx = query.ExecutionInstructionSetIndex()
        r1 = engine.orchestrate(_plan(), clock=FIXED_CLOCK)
        r2 = engine.orchestrate(_plan(status="NOT_PLANNED", execution_intent="NONE"), clock=FIXED_CLOCK)
        idx.ingest(r1)
        idx.ingest(r2)
        assert idx.history() == [r1, r2]

    def test_find_by_id(self):
        idx = query.ExecutionInstructionSetIndex()
        r1 = engine.orchestrate(_plan(), clock=FIXED_CLOCK)
        idx.ingest(r1)
        assert idx.find_by_id(r1.instruction_set_id) == r1
        assert idx.find_by_id("nope") is None

    def test_find_by_status(self):
        idx = query.ExecutionInstructionSetIndex()
        r1 = engine.orchestrate(_plan(status="BLOCKED", strategy_id=None), clock=FIXED_CLOCK)
        idx.ingest(r1)
        assert idx.find_by_status("BLOCKED") == [r1]

    def test_find_by_plan(self):
        idx = query.ExecutionInstructionSetIndex()
        r1 = engine.orchestrate(_plan(plan_id="EP-abc"), clock=FIXED_CLOCK)
        idx.ingest(r1)
        assert idx.find_by_plan("EP-abc") == [r1]

    def test_summary_counts_by_status(self):
        idx = query.ExecutionInstructionSetIndex()
        idx.ingest(engine.orchestrate(_plan(), clock=FIXED_CLOCK))
        idx.ingest(engine.orchestrate(_plan(status="NOT_PLANNED", execution_intent="NONE"), clock=FIXED_CLOCK))
        assert idx.summary() == {"READY_FOR_ADAPTER": 1, "BLOCKED": 1}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_version_matches_package_version(self):
        cfg = config.ExecutionEngineConfig()
        assert cfg.version == taxonomy.EXECUTION_ENGINE_VERSION

    def test_config_is_frozen(self):
        cfg = config.ExecutionEngineConfig()
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

    def test_no_upstream_trading_brain_stage_import_beyond_execution_planner(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for forbidden in (
                        "market_state", "risk_brain", "evidence_interpreter",
                        "strategy_selector", "capital_brain", "ontology",
                    ):
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

    def test_no_broker_sdk_or_order_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    lowered = node.module.lower()
                    for forbidden in ("broker", "order", "fyers", "zerodha", "feed", "replay"):
                        assert forbidden not in lowered

    def test_no_order_or_payload_or_strike_or_quantity_fields(self):
        fields = {f.name for f in models.ExecutionInstructionSet.__dataclass_fields__.values()}
        forbidden = {
            "order_id", "payload", "strike", "expiry", "quantity", "lots",
            "fill_price", "account_balance", "margin",
        }
        assert not (fields & forbidden)

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_no_broker_or_sdk_terms_in_executable_code(self):
        tree = ast.parse(inspect.getsource(engine))
        tree.body = [
            node
            for node in tree.body
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        ]
        code_only = ast.unparse(tree).lower()
        for forbidden in ("fyers", "zerodha", "place_order", "requests.post", "api_key"):
            assert forbidden not in code_only
