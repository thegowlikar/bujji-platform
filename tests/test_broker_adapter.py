"""Tests for the Broker Adapter (FYERS v1) — Engineering Series 40,
Sprint 1.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.execution_engine.models import ExecutionInstructionSet
from bujji.broker_adapter import config, engine, models, query, runner, serialization, taxonomy
from bujji.journal.broker_adapter_journal import BrokerAdapterJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 11, 30, 0)


def _instruction_set(**overrides):
    base = dict(
        instruction_set_id="EIS-0000000000000001",
        status="READY_FOR_ADAPTER",
        execution_intent="PREPARE",
        abstract_actions=("VALIDATE_PLAN", "VALIDATE_CONTROLS", "AUTHORIZE_EXECUTION", "WAIT_FOR_ADAPTER"),
        required_controls=("NONE",),
        blocking_conditions=(),
        execution_trace="stub execution trace",
        confidence="VERY_HIGH",
        plan_id="EP-0000000000000001",
        timestamp="2026-01-01T11:15:00",
        version="1.0.0",
    )
    base.update(overrides)
    return ExecutionInstructionSet(**base)


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_execution_status_values(self):
        assert taxonomy.ALL_EXECUTION_STATUSES == (
            "UNKNOWN", "TRANSLATED", "PARTIALLY_TRANSLATED", "BLOCKED", "FAILED",
        )

    def test_translation_status_values(self):
        assert taxonomy.ALL_TRANSLATION_STATUSES == ("TRANSLATED", "PENDING", "FAILED")

    def test_failure_reason_values(self):
        assert taxonomy.ALL_FAILURE_REASONS == (
            "UNKNOWN_ACTION", "UNSUPPORTED_BROKER", "INVALID_REQUEST",
            "MISSING_FIELD", "ADAPTER_CONFIGURATION_ERROR",
        )

    def test_supported_brokers(self):
        assert taxonomy.ALL_SUPPORTED_BROKERS == ("FYERS",)

    def test_declared_brokers_include_zerodha_for_forward_compat(self):
        assert "ZERODHA" in taxonomy.ALL_DECLARED_BROKERS
        assert "ZERODHA" not in taxonomy.ALL_SUPPORTED_BROKERS

    def test_every_execution_status_has_description(self):
        for s in taxonomy.ALL_EXECUTION_STATUSES:
            assert s in taxonomy.EXECUTION_STATUS_DESCRIPTIONS and taxonomy.EXECUTION_STATUS_DESCRIPTIONS[s]

    def test_every_failure_reason_has_description(self):
        for f in taxonomy.ALL_FAILURE_REASONS:
            assert f in taxonomy.FAILURE_REASON_DESCRIPTIONS and taxonomy.FAILURE_REASON_DESCRIPTIONS[f]


# ---------------------------------------------------------------------------
# FYERS translation
# ---------------------------------------------------------------------------

class TestFyersTranslation:
    def test_ready_for_adapter_translates_all_actions(self):
        result = engine.translate(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK)
        assert result.execution_status == "TRANSLATED"
        assert len(result.translated_actions) == 4

    def test_authorize_execution_maps_to_prepare_place_order(self):
        result = engine.translate(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK)
        authorize = next(a for a in result.translated_actions if a.source_action == "AUTHORIZE_EXECUTION")
        assert authorize.broker_operation == "PREPARE_PLACE_ORDER"
        assert authorize.status == "TRANSLATED"

    def test_wait_for_adapter_makes_no_broker_call(self):
        result = engine.translate(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK)
        wait = next(a for a in result.translated_actions if a.source_action == "WAIT_FOR_ADAPTER")
        assert wait.broker_operation is None
        assert wait.status == "PENDING"

    def test_validate_plan_and_controls_translate(self):
        result = engine.translate(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK)
        vp = next(a for a in result.translated_actions if a.source_action == "VALIDATE_PLAN")
        vc = next(a for a in result.translated_actions if a.source_action == "VALIDATE_CONTROLS")
        assert vp.broker_operation == "VALIDATE_ORDER_PREREQUISITES"
        assert vc.broker_operation == "VALIDATE_ORDER_CONTROLS"

    def test_broker_and_version_recorded(self):
        result = engine.translate(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK)
        assert result.broker == "FYERS"
        assert result.broker_version == "1.0.0"

    def test_blocked_instruction_set_relays_block_without_translation(self):
        blocked = _instruction_set(status="BLOCKED", abstract_actions=("BLOCK",), execution_intent="NONE")
        result = engine.translate(blocked, broker="FYERS", clock=FIXED_CLOCK)
        assert result.execution_status == "BLOCKED"
        assert result.translated_actions[0].broker_operation is None
        assert result.translated_actions[0].status == "PENDING"
        assert result.failure_reasons == ()


# ---------------------------------------------------------------------------
# Unsupported broker
# ---------------------------------------------------------------------------

class TestUnsupportedBroker:
    def test_zerodha_is_unsupported_this_sprint(self):
        result = engine.translate(_instruction_set(), broker="ZERODHA", clock=FIXED_CLOCK)
        assert result.execution_status == "FAILED"
        assert result.failure_reasons == ("UNSUPPORTED_BROKER",)

    def test_unknown_broker_string_is_unsupported(self):
        result = engine.translate(_instruction_set(), broker="NOT_A_REAL_BROKER", clock=FIXED_CLOCK)
        assert result.execution_status == "FAILED"
        assert result.failure_reasons == ("UNSUPPORTED_BROKER",)

    def test_unsupported_broker_translates_nothing(self):
        result = engine.translate(_instruction_set(), broker="ZERODHA", clock=FIXED_CLOCK)
        assert result.translated_actions == ()


# ---------------------------------------------------------------------------
# Missing fields
# ---------------------------------------------------------------------------

class TestMissingFields:
    def test_none_instruction_set_yields_unknown(self):
        result = engine.translate(None, broker="FYERS", clock=FIXED_CLOCK)
        assert result.execution_status == "UNKNOWN"
        assert result.failure_reasons == ("INVALID_REQUEST",)

    def test_unknown_status_instruction_set_yields_unknown(self):
        result = engine.translate(_instruction_set(status="UNKNOWN", abstract_actions=()), broker="FYERS", clock=FIXED_CLOCK)
        assert result.execution_status == "UNKNOWN"
        assert result.failure_reasons == ("INVALID_REQUEST",)

    def test_missing_instruction_set_id_yields_missing_field(self):
        broken = _instruction_set(instruction_set_id="")
        result = engine.translate(broken, broker="FYERS", clock=FIXED_CLOCK)
        assert result.execution_status == "FAILED"
        assert result.failure_reasons == ("MISSING_FIELD",)

    def test_unknown_abstract_action_yields_unknown_action_failure(self):
        weird = _instruction_set(abstract_actions=("EXECUTE",))
        result = engine.translate(weird, broker="FYERS", clock=FIXED_CLOCK)
        assert result.execution_status == "FAILED"
        assert "UNKNOWN_ACTION" in result.failure_reasons
        assert result.translated_actions[0].failure_reason == "UNKNOWN_ACTION"

    def test_partial_translation_when_some_actions_fail(self):
        mixed = _instruction_set(abstract_actions=("VALIDATE_PLAN", "EXECUTE"))
        result = engine.translate(mixed, broker="FYERS", clock=FIXED_CLOCK)
        assert result.execution_status == "PARTIALLY_TRANSLATED"


# ---------------------------------------------------------------------------
# Deterministic translation / replay
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_request_id(self):
        i = _instruction_set()
        r1 = engine.translate(i, broker="FYERS", clock=FIXED_CLOCK)
        r2 = engine.translate(i, broker="FYERS", clock=FIXED_CLOCK)
        assert r1.request_id == r2.request_id
        assert r1 == r2

    def test_byte_identical_across_repeated_runs(self):
        i = _instruction_set(status="BLOCKED", abstract_actions=("BLOCK",), execution_intent="NONE")
        results = [engine.translate(i, broker="FYERS", clock=FIXED_CLOCK) for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_different_input_different_request_id(self):
        r1 = engine.translate(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK)
        r2 = engine.translate(_instruction_set(instruction_set_id="EIS-DIFFERENT0000"), broker="FYERS", clock=FIXED_CLOCK)
        assert r1.request_id != r2.request_id

    def test_request_id_uses_md5_prefix_not_uuid(self):
        result = engine.translate(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK)
        assert result.request_id.startswith("BER-")
        assert len(result.request_id) == len("BER-") + 16

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        result = engine.translate(_instruction_set(), broker="FYERS")
        after = datetime.now()
        parsed = datetime.fromisoformat(result.timestamp)
        assert before <= parsed <= after

    def test_no_randomness_module_used(self):
        source = inspect.getsource(engine)
        assert "import random" not in source
        assert "random." not in source


# ---------------------------------------------------------------------------
# Immutable dataclasses / no mutation
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_request_is_frozen(self):
        result = engine.translate(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            result.execution_status = "HACKED"

    def test_translated_action_is_frozen(self):
        result = engine.translate(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            result.translated_actions[0].broker_operation = "HACKED"

    def test_engine_does_not_mutate_input(self):
        i = _instruction_set()
        i_copy = ExecutionInstructionSet(**{f.name: getattr(i, f.name) for f in i.__dataclass_fields__.values()})
        engine.translate(i, broker="FYERS", clock=FIXED_CLOCK)
        assert i == i_copy


# ---------------------------------------------------------------------------
# Serialization round trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        result = engine.translate(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK)
        d = serialization.request_to_dict(result)
        back = serialization.request_from_dict(d)
        assert back == result

    def test_round_trip_is_json_safe(self):
        import json

        result = engine.translate(_instruction_set(abstract_actions=("VALIDATE_PLAN", "EXECUTE")), broker="FYERS", clock=FIXED_CLOCK)
        d = serialization.request_to_dict(result)
        text = json.dumps(d)
        back = serialization.request_from_dict(json.loads(text))
        assert back == result

    def test_round_trip_with_none_instruction_set_id(self):
        result = engine.translate(None, broker="FYERS", clock=FIXED_CLOCK)
        d = serialization.request_to_dict(result)
        assert d["instruction_set_id"] is None
        back = serialization.request_from_dict(d)
        assert back.instruction_set_id is None


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = BrokerAdapterJournal(tmp_path / "ba.jsonl")
        result = engine.translate(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK)
        j.record(result)
        assert j.read_all() == [result]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = BrokerAdapterJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = BrokerAdapterJournal(tmp_path / "j.jsonl")
        r1 = engine.translate(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK)
        r2 = engine.translate(None, broker="FYERS", clock=FIXED_CLOCK)
        j.record_many([r1, r2])
        records = j.read_all()
        assert [r.request_id for r in records] == [r1.request_id, r2.request_id]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(BrokerAdapterJournal)
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
# runner.run_translation composition
# ---------------------------------------------------------------------------

class TestRunner:
    def test_run_without_journal(self):
        result = runner.run_translation(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK)
        assert isinstance(result, models.BrokerExecutionRequest)

    def test_run_journals_when_given_one(self, tmp_path):
        j = BrokerAdapterJournal(tmp_path / "run.jsonl")
        result = runner.run_translation(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK, journal=j)
        assert j.read_all() == [result]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestBrokerExecutionRequestIndex:
    def test_ingest_and_latest(self):
        idx = query.BrokerExecutionRequestIndex()
        r1 = engine.translate(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK)
        idx.ingest(r1)
        assert idx.latest() == r1

    def test_latest_none_when_empty(self):
        assert query.BrokerExecutionRequestIndex().latest() is None

    def test_find_by_broker(self):
        idx = query.BrokerExecutionRequestIndex()
        r1 = engine.translate(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK)
        idx.ingest(r1)
        assert idx.find_by_broker("FYERS") == [r1]

    def test_find_by_status(self):
        idx = query.BrokerExecutionRequestIndex()
        r1 = engine.translate(_instruction_set(), broker="ZERODHA", clock=FIXED_CLOCK)
        idx.ingest(r1)
        assert idx.find_by_status("FAILED") == [r1]

    def test_summary_counts_by_status(self):
        idx = query.BrokerExecutionRequestIndex()
        idx.ingest(engine.translate(_instruction_set(), broker="FYERS", clock=FIXED_CLOCK))
        idx.ingest(engine.translate(_instruction_set(), broker="ZERODHA", clock=FIXED_CLOCK))
        summary = idx.summary()
        assert summary.get("TRANSLATED") == 1
        assert summary.get("FAILED") == 1


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_version_matches_package_version(self):
        cfg = config.BrokerAdapterConfig()
        assert cfg.version == taxonomy.BROKER_ADAPTER_VERSION

    def test_default_broker_is_fyers(self):
        cfg = config.BrokerAdapterConfig()
        assert cfg.default_broker == "FYERS"

    def test_config_is_frozen(self):
        cfg = config.BrokerAdapterConfig()
        with pytest.raises(Exception):
            cfg.version = "9.9.9"


# ---------------------------------------------------------------------------
# Isolation firewall
# ---------------------------------------------------------------------------

FORBIDDEN_MODULE_SUBSTRINGS = (
    "bujji.broker", "bujji.core.models", "bujji.execution", "bujji.trade",
    "fyers_apiv3", "requests", "websocket", "mic_v2",
)


def _module_source_files():
    base = Path(engine.__file__).parent
    return list(base.glob("*.py"))


class TestIsolation:
    def test_no_production_broker_or_sdk_import(self):
        for path in _module_source_files():
            source = path.read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    for forbidden in FORBIDDEN_MODULE_SUBSTRINGS:
                        assert forbidden not in name, f"{path.name} imports forbidden {name}"

    def test_no_upstream_trading_brain_stage_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for forbidden in (
                        "market_state", "risk_brain", "evidence_interpreter",
                        "strategy_selector", "capital_brain", "ontology", "execution_planner",
                    ):
                        assert forbidden not in node.module

    def test_no_order_or_strike_or_quantity_or_token_fields(self):
        request_fields = {f.name for f in models.BrokerExecutionRequest.__dataclass_fields__.values()}
        action_fields = {f.name for f in models.TranslatedAction.__dataclass_fields__.values()}
        forbidden = {
            "order_id", "client_order_id", "strike", "expiry", "quantity", "lots",
            "access_token", "refresh_token", "app_secret", "account_balance",
        }
        assert not (request_fields & forbidden)
        assert not (action_fields & forbidden)

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_no_broker_connection_or_order_placement_capability_in_source(self):
        for path in _module_source_files():
            source = path.read_text()
            for forbidden in (
                "def connect(", "def place_order(", "def authenticate(",
                "requests.get(", "requests.post(", "websocket.", "def poll(",
            ):
                assert forbidden not in source

    def test_no_broker_terms_in_executable_code(self):
        tree = ast.parse(inspect.getsource(engine))
        tree.body = [
            node
            for node in tree.body
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        ]
        code_only = ast.unparse(tree).lower()
        for forbidden in ("fyers_apiv3", "requests.", "api_key", "access_token"):
            assert forbidden not in code_only
