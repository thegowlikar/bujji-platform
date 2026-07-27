"""Tests for the Runtime Execution Orchestrator v1 — Engineering
Series 45, Sprint 1.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.nifty_contract_builder.models import NiftyOptionContract
from bujji.trading_brain.order_construction.models import OrderRequest, OrderTags
from bujji.runtime_execution import config as config_module
from bujji.runtime_execution import engine, models, query, runner, serialization, taxonomy
from bujji.journal.runtime_execution_journal import RuntimeExecutionJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 35, 0)


def _contract(strike, option_type, side, **overrides):
    base = dict(
        contract_id=f"NC-{strike}{option_type}{side}",
        underlying="NIFTY",
        expiry="2026-07-31",
        strike=strike,
        option_type=option_type,
        side=side,
        contract_symbol=f"NSE:NIFTY2026-07-31{strike}{option_type}",
        capital_intent="STANDARD",
        strategy_id="PREMIUM_VWAP_STRADDLE",
        selection_reason="stub selection reason",
        construction_trace="stub construction trace",
        timestamp="2026-01-01T09:20:00",
        version="1.0.0",
    )
    base.update(overrides)
    return NiftyOptionContract(**base)


def _tags(**overrides):
    base = dict(
        strategy_id="PREMIUM_VWAP_STRADDLE",
        session_id="SESSION-0001",
        pipeline_version="1.0.0",
        qualification_fingerprint="FP-abc123",
    )
    base.update(overrides)
    return OrderTags(**base)


def _order(strike, option_type, side, client_order_id=None, **overrides):
    coid = client_order_id if client_order_id is not None else f"COID-{strike}{option_type}{side}"
    base = dict(
        request_id=f"OR-{strike}{option_type}{side}",
        contract=_contract(strike, option_type, side),
        side=side,
        quantity=150,
        order_type="MARKET",
        product="MIS",
        validity="DAY",
        execution_policy="MARKET",
        client_order_id=coid,
        tags=_tags(),
        creation_trace="stub creation trace",
        timestamp="2026-01-01T09:30:00",
        version="1.0.0",
    )
    base.update(overrides)
    return OrderRequest(**base)


def _straddle_orders():
    return (
        _order(25150, "CE", "SELL"),
        _order(25150, "PE", "SELL"),
    )


class _StubExecutor:
    def __init__(self, should_raise=False):
        self.should_raise = should_raise
        self.calls = []

    def submit_and_confirm(self, order_request):
        self.calls.append(order_request)
        if self.should_raise:
            raise RuntimeError("stub failure")
        return object()


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_execution_states(self):
        assert taxonomy.ALL_EXECUTION_STATES == (
            "CREATED", "VALIDATED", "READY", "DISPATCH_PENDING",
            "DISPATCHED", "FAILED_VALIDATION", "ABORTED",
        )

    def test_failure_reasons(self):
        assert taxonomy.ALL_FAILURE_REASONS == (
            "EMPTY_SESSION", "INVALID_ORDER_REQUEST", "DUPLICATE_CLIENT_ORDER_ID",
            "INVALID_DISPATCH_PLAN", "INSUFFICIENT_DATA",
        )

    def test_order_type_reused_from_order_construction(self):
        from bujji.trading_brain.order_construction.taxonomy import ALL_EXECUTION_POLICIES
        assert taxonomy.REUSED_ORDER_TYPES == ALL_EXECUTION_POLICIES

    def test_every_execution_state_has_description(self):
        for s in taxonomy.ALL_EXECUTION_STATES:
            assert s in taxonomy.EXECUTION_STATE_DESCRIPTIONS and taxonomy.EXECUTION_STATE_DESCRIPTIONS[s]

    def test_every_failure_reason_has_description(self):
        for f in taxonomy.ALL_FAILURE_REASONS:
            assert f in taxonomy.FAILURE_REASON_DESCRIPTIONS and taxonomy.FAILURE_REASON_DESCRIPTIONS[f]


# ---------------------------------------------------------------------------
# Single-order sessions
# ---------------------------------------------------------------------------

class TestSingleOrderSessions:
    def test_single_order_builds_ready_session(self):
        session = engine.build_session((_order(25150, "CE", "BUY"),), clock=FIXED_CLOCK)
        assert session.execution_state == "READY"
        assert session.validation_result == "PASSED"
        assert len(session.dispatch_plan) == 1

    def test_single_order_dispatch_instruction_matches(self):
        order = _order(25150, "CE", "BUY")
        session = engine.build_session((order,), clock=FIXED_CLOCK)
        assert session.dispatch_plan[0].order_request == order
        assert session.dispatch_plan[0].client_order_id == order.client_order_id


# ---------------------------------------------------------------------------
# Multi-leg sessions
# ---------------------------------------------------------------------------

class TestMultiLegSessions:
    def test_straddle_two_orders(self):
        session = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        assert session.execution_state == "READY"
        assert len(session.dispatch_plan) == 2

    def test_iron_fly_four_orders(self):
        orders = (
            _order(25150, "CE", "SELL"),
            _order(25150, "PE", "SELL"),
            _order(25200, "CE", "BUY"),
            _order(25100, "PE", "BUY"),
        )
        session = engine.build_session(orders, clock=FIXED_CLOCK)
        assert session.execution_state == "READY"
        assert len(session.dispatch_plan) == 4

    def test_dispatch_plan_sequence_matches_order(self):
        orders = _straddle_orders()
        session = engine.build_session(orders, clock=FIXED_CLOCK)
        sequences = [d.sequence for d in session.dispatch_plan]
        assert sequences == [0, 1]


# ---------------------------------------------------------------------------
# Atomic validation failures
# ---------------------------------------------------------------------------

class TestAtomicValidationFailures:
    def test_one_bad_leg_fails_whole_session(self):
        good = _order(25150, "CE", "SELL")
        bad = _order(25150, "PE", "SELL", quantity=0)
        session = engine.build_session((good, bad), clock=FIXED_CLOCK)
        assert session.execution_state == "FAILED_VALIDATION"
        assert session.failure_reason == "INVALID_ORDER_REQUEST"
        assert session.dispatch_plan == ()

    def test_unrecognized_order_type_fails(self):
        bad = _order(25150, "CE", "SELL", order_type="NOT_A_REAL_TYPE")
        session = engine.build_session((bad,), clock=FIXED_CLOCK)
        assert session.failure_reason == "INVALID_ORDER_REQUEST"

    def test_missing_contract_fails(self):
        bad = _order(25150, "CE", "SELL", contract=None)
        session = engine.build_session((bad,), clock=FIXED_CLOCK)
        assert session.failure_reason == "INVALID_ORDER_REQUEST"

    def test_empty_client_order_id_fails(self):
        bad = _order(25150, "CE", "SELL", client_order_id="")
        session = engine.build_session((bad,), clock=FIXED_CLOCK)
        assert session.failure_reason == "INVALID_ORDER_REQUEST"


# ---------------------------------------------------------------------------
# Duplicate client order IDs
# ---------------------------------------------------------------------------

class TestDuplicateClientOrderIds:
    def test_duplicate_ids_fail(self):
        o1 = _order(25150, "CE", "SELL", client_order_id="COID-SAME")
        o2 = _order(25150, "PE", "SELL", client_order_id="COID-SAME")
        session = engine.build_session((o1, o2), clock=FIXED_CLOCK)
        assert session.execution_state == "FAILED_VALIDATION"
        assert session.failure_reason == "DUPLICATE_CLIENT_ORDER_ID"
        assert session.dispatch_plan == ()


# ---------------------------------------------------------------------------
# Empty session
# ---------------------------------------------------------------------------

class TestEmptySession:
    def test_empty_tuple_fails(self):
        session = engine.build_session((), clock=FIXED_CLOCK)
        assert session.execution_state == "FAILED_VALIDATION"
        assert session.failure_reason == "EMPTY_SESSION"

    def test_none_order_requests_is_aborted(self):
        session = engine.build_session(None, clock=FIXED_CLOCK)
        assert session.execution_state == "ABORTED"
        assert session.failure_reason == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# Queue / dispatch
# ---------------------------------------------------------------------------

class TestQueueAndDispatch:
    def test_ready_session_queues_to_dispatch_pending(self):
        session = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        queued = engine.queue_for_dispatch(session, clock=FIXED_CLOCK)
        assert queued.execution_state == "DISPATCH_PENDING"

    def test_queue_is_noop_on_non_ready_session(self):
        session = engine.build_session((), clock=FIXED_CLOCK)
        queued = engine.queue_for_dispatch(session, clock=FIXED_CLOCK)
        assert queued == session

    def test_dispatch_success_transitions_to_dispatched(self):
        session = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        queued = engine.queue_for_dispatch(session, clock=FIXED_CLOCK)
        executor = _StubExecutor()
        result = engine.dispatch(queued, executor, clock=FIXED_CLOCK)
        assert result.execution_state == "DISPATCHED"
        assert len(executor.calls) == 2

    def test_dispatch_failure_transitions_to_aborted(self):
        session = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        queued = engine.queue_for_dispatch(session, clock=FIXED_CLOCK)
        executor = _StubExecutor(should_raise=True)
        result = engine.dispatch(queued, executor, clock=FIXED_CLOCK)
        assert result.execution_state == "ABORTED"

    def test_dispatch_is_noop_on_wrong_state(self):
        session = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)  # READY, not DISPATCH_PENDING
        executor = _StubExecutor()
        result = engine.dispatch(session, executor, clock=FIXED_CLOCK)
        assert result == session
        assert executor.calls == []

    def test_executor_interface_is_runtime_checkable_protocol(self):
        assert isinstance(_StubExecutor(), engine.ExecutionEngineInterface)


# ---------------------------------------------------------------------------
# Traceability
# ---------------------------------------------------------------------------

class TestTraceability:
    def test_trace_mentions_order_count_and_state(self):
        session = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        assert "OrderRequest x2" in session.execution_trace
        assert "READY" in session.execution_trace


# ---------------------------------------------------------------------------
# Determinism / replay
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_session_id(self):
        s1 = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        s2 = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        assert s1.session_id == s2.session_id
        assert s1 == s2

    def test_byte_identical_across_repeated_runs(self):
        results = [engine.build_session(_straddle_orders(), clock=FIXED_CLOCK) for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_different_inputs_different_session_id(self):
        s1 = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        s2 = engine.build_session((_order(25150, "CE", "BUY"),), clock=FIXED_CLOCK)
        assert s1.session_id != s2.session_id

    def test_session_id_uses_md5_prefix_not_uuid(self):
        session = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        assert session.session_id.startswith("ES-")
        assert len(session.session_id) == len("ES-") + 16

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        session = engine.build_session(_straddle_orders())
        after = datetime.now()
        parsed = datetime.fromisoformat(session.timestamp)
        assert before <= parsed <= after

    def test_no_randomness_module_used(self):
        source = inspect.getsource(engine)
        assert "import random" not in source
        assert "random." not in source


# ---------------------------------------------------------------------------
# Frozen dataclasses / no mutation
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_session_is_frozen(self):
        session = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            session.execution_state = "HACKED"

    def test_dispatch_instruction_is_frozen(self):
        session = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            session.dispatch_plan[0].sequence = 99

    def test_engine_does_not_mutate_inputs(self):
        orders = _straddle_orders()
        orders_copy = tuple(
            OrderRequest(**{f.name: getattr(o, f.name) for f in o.__dataclass_fields__.values()})
            for o in orders
        )
        engine.build_session(orders, clock=FIXED_CLOCK)
        assert orders == orders_copy


# ---------------------------------------------------------------------------
# Serialization round trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        session = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        d = serialization.session_to_dict(session)
        back = serialization.session_from_dict(d)
        assert back == session

    def test_round_trip_is_json_safe(self):
        import json

        session = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        d = serialization.session_to_dict(session)
        text = json.dumps(d)
        back = serialization.session_from_dict(json.loads(text))
        assert back == session

    def test_round_trip_with_failure(self):
        session = engine.build_session((), clock=FIXED_CLOCK)
        d = serialization.session_to_dict(session)
        assert d["dispatch_plan"] == []
        back = serialization.session_from_dict(d)
        assert back == session


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = RuntimeExecutionJournal(tmp_path / "re.jsonl")
        session = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        j.record(session)
        assert j.read_all() == [session]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = RuntimeExecutionJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = RuntimeExecutionJournal(tmp_path / "j.jsonl")
        s1 = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        s2 = engine.build_session((), clock=FIXED_CLOCK)
        j.record_many([s1, s2])
        records = j.read_all()
        assert [r.session_id for r in records] == [s1.session_id, s2.session_id]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(RuntimeExecutionJournal)
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
# runner composition
# ---------------------------------------------------------------------------

class TestRunner:
    def test_run_session_build_without_journal(self):
        session = runner.run_session_build(_straddle_orders(), clock=FIXED_CLOCK)
        assert isinstance(session, models.ExecutionSession)

    def test_run_session_build_journals_when_given_one(self, tmp_path):
        j = RuntimeExecutionJournal(tmp_path / "run.jsonl")
        session = runner.run_session_build(_straddle_orders(), clock=FIXED_CLOCK, journal=j)
        assert j.read_all() == [session]

    def test_run_session_dispatch_without_executor_stops_at_pending(self):
        session = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        result = runner.run_session_dispatch(session, executor=None, clock=FIXED_CLOCK)
        assert result.execution_state == "DISPATCH_PENDING"

    def test_run_session_dispatch_with_executor_reaches_dispatched(self):
        session = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        result = runner.run_session_dispatch(session, executor=_StubExecutor(), clock=FIXED_CLOCK)
        assert result.execution_state == "DISPATCHED"


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestExecutionSessionIndex:
    def test_ingest_and_latest(self):
        idx = query.ExecutionSessionIndex()
        s1 = engine.build_session(_straddle_orders(), clock=FIXED_CLOCK)
        idx.ingest(s1)
        assert idx.latest() == s1

    def test_find_by_state(self):
        idx = query.ExecutionSessionIndex()
        s1 = engine.build_session((), clock=FIXED_CLOCK)
        idx.ingest(s1)
        assert idx.find_by_state("FAILED_VALIDATION") == [s1]

    def test_summary_counts_by_state(self):
        idx = query.ExecutionSessionIndex()
        idx.ingest(engine.build_session(_straddle_orders(), clock=FIXED_CLOCK))
        idx.ingest(engine.build_session((), clock=FIXED_CLOCK))
        summary = idx.summary()
        assert summary.get("READY") == 1
        assert summary.get("FAILED_VALIDATION") == 1


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_version_matches_package_version(self):
        cfg = config_module.RuntimeExecutionConfig()
        assert cfg.version == taxonomy.RUNTIME_EXECUTION_VERSION

    def test_config_is_frozen(self):
        cfg = config_module.RuntimeExecutionConfig()
        with pytest.raises(Exception):
            cfg.version = "9.9.9"


# ---------------------------------------------------------------------------
# Isolation firewall
# ---------------------------------------------------------------------------

FORBIDDEN_BUJJI_SUBPACKAGES = {
    "broker", "core", "trade", "intelligence", "market", "tick",
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

    def test_no_production_execution_engine_or_broker_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    lowered = node.module.lower()
                    for forbidden in ("bujji.execution", "bujji.broker", "fyers", "zerodha"):
                        assert forbidden not in lowered

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

    def test_no_upstream_trading_brain_stage_import_beyond_order_construction(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for forbidden in (
                        "market_state", "risk_brain", "evidence_interpreter",
                        "strategy_selector", "capital_brain", "ontology",
                        "execution_planner", "execution_engine", "position_sizing",
                    ):
                        assert forbidden not in node.module

    def test_no_auth_or_retry_or_broker_order_id_fields(self):
        session_fields = {f.name for f in models.ExecutionSession.__dataclass_fields__.values()}
        forbidden = {"access_token", "refresh_token", "auth_token", "retry_count", "broker_order_id", "fill_price"}
        assert not (session_fields & forbidden)

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_no_network_or_authentication_capability_in_source(self):
        for path in _module_source_files():
            source = path.read_text()
            for forbidden in (
                "requests.get(", "requests.post(", "websocket.",
                "def authenticate(", "def connect(", "def refresh_token(", "def poll(",
            ):
                assert forbidden not in source

    def test_no_network_terms_in_executable_code(self):
        tree = ast.parse(inspect.getsource(engine))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    node.body[0] = ast.Pass()
        ast.fix_missing_locations(tree)
        code_only = ast.unparse(tree).lower()
        for forbidden in ("fyers", "zerodha", "requests.", "websocket", "api_key"):
            assert forbidden not in code_only
