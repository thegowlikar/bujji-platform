"""Tests for the Runtime Safety Gate — Engineering Series 47, Sprint 1."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.trading_brain.nifty_contract_builder.models import NiftyOptionContract
from bujji.trading_brain.order_construction.models import OrderRequest, OrderTags
from bujji.runtime_execution.models import DispatchInstruction, ExecutionSession
from bujji.runtime_safety import config as config_module
from bujji.runtime_safety import engine, models, query, runner, serialization, taxonomy
from bujji.journal.runtime_safety_journal import RuntimeSafetyJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 12, 30, 0)


def _contract(strike=25150, option_type="CE", side="SELL"):
    return NiftyOptionContract(
        contract_id=f"NC-{strike}{option_type}{side}", underlying="NIFTY", expiry="2026-07-31",
        strike=strike, option_type=option_type, side=side,
        contract_symbol=f"NSE:NIFTY{strike}{option_type}", capital_intent="STANDARD",
        strategy_id="PREMIUM_VWAP_STRADDLE", selection_reason="x", construction_trace="x",
        timestamp="2026-01-01T09:00:00", version="1.0.0",
    )


def _order(client_order_id, strike=25150, option_type="CE", side="SELL"):
    tags = OrderTags(strategy_id="X", session_id="S", pipeline_version="1.0.0", qualification_fingerprint="FP")
    return OrderRequest(
        request_id=f"OR-{client_order_id}", contract=_contract(strike, option_type, side), side=side,
        quantity=75, order_type="MARKET", product="MIS", validity="DAY", execution_policy="MARKET",
        client_order_id=client_order_id, tags=tags, creation_trace="x",
        timestamp="2026-01-01T09:00:00", version="1.0.0",
    )


def _session(execution_state="DISPATCHED", orders=None, dispatch_plan=None, version="1.0.0"):
    orders = orders if orders is not None else (_order("COID-1"), _order("COID-2", strike=25200))
    if dispatch_plan is None:
        dispatch_plan = tuple(
            DispatchInstruction(sequence=i, client_order_id=o.client_order_id, order_request=o)
            for i, o in enumerate(orders)
        )
    return ExecutionSession(
        session_id="ES-0000000000000001", order_requests=orders, execution_state=execution_state,
        dispatch_plan=dispatch_plan, validation_result="PASSED", execution_trace="x",
        failure_reason=None, timestamp="2026-01-01T09:30:00", version=version,
    )


def _qual_policy(**overrides):
    base = dict(replay_status="PASSED", qualification_fingerprint="RFP-abc123", chain_valid=True, deterministic=True)
    base.update(overrides)
    return models.QualificationPolicy(**base)


def _safety_policy(**overrides):
    base = dict(policy_version="1.0.0", allow_authorized_with_warnings=True)
    base.update(overrides)
    return models.RuntimeSafetyPolicy(**base)


def _authorize(session=None, qual=None, safety=None):
    return engine.authorize(
        session if session is not None else _session(),
        qual if qual is not None else _qual_policy(),
        safety if safety is not None else _safety_policy(),
        clock=FIXED_CLOCK,
    )


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_authorization_states(self):
        assert taxonomy.ALL_AUTHORIZATION_STATES == (
            "AUTHORIZED", "AUTHORIZED_WITH_WARNINGS", "DENIED", "INSUFFICIENT_DATA",
        )

    def test_decisions(self):
        assert taxonomy.ALL_DECISIONS == ("ALLOW", "DENY", "UNKNOWN")

    def test_failure_reasons(self):
        assert taxonomy.ALL_FAILURE_REASONS == (
            "INVALID_SESSION", "MISSING_FINGERPRINT", "FAILED_REPLAY", "INVALID_CONFIGURATION",
            "EMPTY_DISPATCH_PLAN", "DUPLICATE_CLIENT_ORDER_ID", "INSUFFICIENT_DATA",
        )

    def test_safety_checks_count(self):
        assert len(taxonomy.ALL_SAFETY_CHECKS) == 9

    def test_every_check_maps_to_a_failure_reason(self):
        for check in taxonomy.ALL_SAFETY_CHECKS:
            assert check in taxonomy.FAILURE_REASON_BY_CHECK


# ---------------------------------------------------------------------------
# Authorized / authorized with warnings
# ---------------------------------------------------------------------------

class TestAuthorizedSession:
    def test_dispatched_session_is_authorized(self):
        result = _authorize(session=_session(execution_state="DISPATCHED"))
        assert result.authorization_state == "AUTHORIZED"
        assert result.decision == "ALLOW"
        assert result.failed_checks == ()
        assert len(result.passed_checks) == 9

    def test_authorized_has_no_warnings(self):
        result = _authorize(session=_session(execution_state="DISPATCHED"))
        assert result.warnings == ()


class TestAuthorizedWithWarnings:
    def test_ready_session_is_authorized_with_warnings(self):
        result = _authorize(session=_session(execution_state="READY"))
        assert result.authorization_state == "AUTHORIZED_WITH_WARNINGS"
        assert result.decision == "ALLOW"
        assert "SESSION_NOT_YET_DISPATCHED" in result.warnings

    def test_dispatch_pending_session_is_authorized_with_warnings(self):
        result = _authorize(session=_session(execution_state="DISPATCH_PENDING"))
        assert result.authorization_state == "AUTHORIZED_WITH_WARNINGS"

    def test_warnings_disabled_by_policy_still_authorizes_cleanly(self):
        result = _authorize(session=_session(execution_state="READY"), safety=_safety_policy(allow_authorized_with_warnings=False))
        assert result.authorization_state == "AUTHORIZED"
        assert result.warnings == ()


# ---------------------------------------------------------------------------
# Denied session
# ---------------------------------------------------------------------------

class TestDeniedSession:
    def test_failed_validation_session_is_denied(self):
        session = _session(execution_state="FAILED_VALIDATION")
        result = _authorize(session=session)
        assert result.authorization_state == "DENIED"
        assert result.decision == "DENY"
        assert "INVALID_SESSION" in result.failure_reasons

    def test_aborted_session_is_denied(self):
        result = _authorize(session=_session(execution_state="ABORTED"))
        assert result.authorization_state == "DENIED"


# ---------------------------------------------------------------------------
# Missing fingerprint
# ---------------------------------------------------------------------------

class TestMissingFingerprint:
    def test_none_fingerprint_is_denied(self):
        result = _authorize(qual=_qual_policy(qualification_fingerprint=None))
        assert result.authorization_state == "DENIED"
        assert "MISSING_FINGERPRINT" in result.failure_reasons
        assert "QUALIFICATION_FINGERPRINT_PRESENT" in result.failed_checks

    def test_empty_string_fingerprint_is_denied(self):
        result = _authorize(qual=_qual_policy(qualification_fingerprint=""))
        assert "MISSING_FINGERPRINT" in result.failure_reasons


# ---------------------------------------------------------------------------
# Duplicate client order IDs
# ---------------------------------------------------------------------------

class TestDuplicateClientOrderIds:
    def test_duplicate_ids_denied(self):
        orders = (_order("COID-SAME"), _order("COID-SAME", strike=25200))
        session = _session(orders=orders)
        result = _authorize(session=session)
        assert result.authorization_state == "DENIED"
        assert "DUPLICATE_CLIENT_ORDER_ID" in result.failure_reasons
        assert "CLIENT_ORDER_IDS_UNIQUE" in result.failed_checks


# ---------------------------------------------------------------------------
# Empty dispatch plan
# ---------------------------------------------------------------------------

class TestEmptyDispatchPlan:
    def test_empty_plan_denied(self):
        session = _session(orders=(), dispatch_plan=())
        result = _authorize(session=session)
        assert result.authorization_state == "DENIED"
        assert "EMPTY_DISPATCH_PLAN" in result.failure_reasons
        assert "DISPATCH_PLAN_NOT_EMPTY" in result.failed_checks


# ---------------------------------------------------------------------------
# Replay failure
# ---------------------------------------------------------------------------

class TestReplayFailure:
    def test_non_passed_replay_status_denied(self):
        result = _authorize(qual=_qual_policy(replay_status="FAILED"))
        assert result.authorization_state == "DENIED"
        assert "FAILED_REPLAY" in result.failure_reasons

    def test_non_chain_valid_denied(self):
        result = _authorize(qual=_qual_policy(chain_valid=False))
        assert result.authorization_state == "DENIED"
        assert "FAILED_REPLAY" in result.failure_reasons

    def test_non_deterministic_denied(self):
        result = _authorize(qual=_qual_policy(deterministic=False))
        assert result.authorization_state == "DENIED"
        assert "FAILED_REPLAY" in result.failure_reasons


class TestInvalidConfiguration:
    def test_unrecognized_session_version_denied(self):
        result = _authorize(session=_session(version="9.9.9"))
        assert result.authorization_state == "DENIED"
        assert "INVALID_CONFIGURATION" in result.failure_reasons

    def test_unrecognized_policy_version_denied(self):
        result = _authorize(safety=_safety_policy(policy_version="9.9.9"))
        assert result.authorization_state == "DENIED"
        assert "INVALID_CONFIGURATION" in result.failure_reasons


class TestInsufficientData:
    def test_none_session_yields_insufficient_data(self):
        result = engine.authorize(None, _qual_policy(), _safety_policy(), clock=FIXED_CLOCK)
        assert result.authorization_state == "INSUFFICIENT_DATA"
        assert result.decision == "UNKNOWN"

    def test_none_qualification_policy_yields_insufficient_data(self):
        result = engine.authorize(_session(), None, _safety_policy(), clock=FIXED_CLOCK)
        assert result.authorization_state == "INSUFFICIENT_DATA"

    def test_none_safety_policy_yields_insufficient_data(self):
        result = engine.authorize(_session(), _qual_policy(), None, clock=FIXED_CLOCK)
        assert result.authorization_state == "INSUFFICIENT_DATA"

    def test_insufficient_data_never_fabricates_checks(self):
        result = engine.authorize(None, None, None, clock=FIXED_CLOCK)
        assert result.passed_checks == ()
        assert result.failed_checks == ()


# ---------------------------------------------------------------------------
# Traceability
# ---------------------------------------------------------------------------

class TestTraceability:
    def test_trace_mentions_authorization_state(self):
        result = _authorize()
        assert result.authorization_state in result.authorization_trace

    def test_trace_mentions_failed_checks_when_denied(self):
        result = _authorize(qual=_qual_policy(qualification_fingerprint=None))
        assert "QUALIFICATION_FINGERPRINT_PRESENT" in result.authorization_trace


# ---------------------------------------------------------------------------
# Determinism / replay
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_authorization_id(self):
        r1 = _authorize()
        r2 = _authorize()
        assert r1.authorization_id == r2.authorization_id
        assert r1 == r2

    def test_byte_identical_across_repeated_runs(self):
        results = [_authorize(session=_session(execution_state="READY")) for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_different_inputs_different_authorization_id(self):
        r1 = _authorize()
        r2 = _authorize(qual=_qual_policy(replay_status="FAILED"))
        assert r1.authorization_id != r2.authorization_id

    def test_authorization_id_uses_md5_prefix_not_uuid(self):
        result = _authorize()
        assert result.authorization_id.startswith("RAUTH-")

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        result = engine.authorize(_session(), _qual_policy(), _safety_policy())
        after = datetime.now()
        parsed = datetime.fromisoformat(result.timestamp)
        assert before <= parsed <= after

    def test_no_randomness_module_used(self):
        source = inspect.getsource(engine)
        assert "import random" not in source
        assert "random." not in source


# ---------------------------------------------------------------------------
# Immutable dataclasses
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_authorization_is_frozen(self):
        result = _authorize()
        with pytest.raises(Exception):
            result.authorization_state = "HACKED"

    def test_engine_does_not_mutate_inputs(self):
        session = _session()
        session_copy = ExecutionSession(**{f.name: getattr(session, f.name) for f in session.__dataclass_fields__.values()})
        _authorize(session=session)
        assert session == session_copy


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        result = _authorize(session=_session(execution_state="READY"))
        d = serialization.authorization_to_dict(result)
        back = serialization.authorization_from_dict(d)
        assert back == result

    def test_round_trip_is_json_safe(self):
        import json

        result = _authorize()
        d = serialization.authorization_to_dict(result)
        text = json.dumps(d)
        back = serialization.authorization_from_dict(json.loads(text))
        assert back == result

    def test_qualification_policy_round_trip(self):
        p = _qual_policy()
        d = serialization.qualification_policy_to_dict(p)
        assert serialization.qualification_policy_from_dict(d) == p

    def test_runtime_safety_policy_round_trip(self):
        p = _safety_policy()
        d = serialization.runtime_safety_policy_to_dict(p)
        assert serialization.runtime_safety_policy_from_dict(d) == p


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = RuntimeSafetyJournal(tmp_path / "rs.jsonl")
        result = _authorize()
        j.record(result)
        assert j.read_all() == [result]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = RuntimeSafetyJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = RuntimeSafetyJournal(tmp_path / "j.jsonl")
        r1 = _authorize()
        r2 = _authorize(qual=_qual_policy(replay_status="FAILED"))
        j.record_many([r1, r2])
        records = j.read_all()
        assert [r.authorization_id for r in records] == [r1.authorization_id, r2.authorization_id]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(RuntimeSafetyJournal)
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
    def test_run_without_journal(self):
        result = runner.run_authorization(_session(), _qual_policy(), _safety_policy(), clock=FIXED_CLOCK)
        assert isinstance(result, models.RuntimeAuthorization)

    def test_run_journals_when_given_one(self, tmp_path):
        j = RuntimeSafetyJournal(tmp_path / "run.jsonl")
        result = runner.run_authorization(_session(), _qual_policy(), _safety_policy(), clock=FIXED_CLOCK, journal=j)
        assert j.read_all() == [result]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestRuntimeAuthorizationIndex:
    def test_ingest_and_latest(self):
        idx = query.RuntimeAuthorizationIndex()
        r1 = _authorize()
        idx.ingest(r1)
        assert idx.latest() == r1

    def test_find_by_state(self):
        idx = query.RuntimeAuthorizationIndex()
        r1 = _authorize(qual=_qual_policy(replay_status="FAILED"))
        idx.ingest(r1)
        assert idx.find_by_state("DENIED") == [r1]

    def test_find_by_session(self):
        idx = query.RuntimeAuthorizationIndex()
        r1 = _authorize()
        idx.ingest(r1)
        assert idx.find_by_session("ES-0000000000000001") == [r1]

    def test_summary_counts_by_state(self):
        idx = query.RuntimeAuthorizationIndex()
        idx.ingest(_authorize())
        idx.ingest(_authorize(qual=_qual_policy(replay_status="FAILED")))
        summary = idx.summary()
        assert summary.get("AUTHORIZED") == 1
        assert summary.get("DENIED") == 1


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_version_matches_package_version(self):
        cfg = config_module.RuntimeSafetyConfig()
        assert cfg.version == taxonomy.RUNTIME_SAFETY_VERSION

    def test_config_is_frozen(self):
        cfg = config_module.RuntimeSafetyConfig()
        with pytest.raises(Exception):
            cfg.version = "9.9.9"


# ---------------------------------------------------------------------------
# Isolation firewall
# ---------------------------------------------------------------------------

FORBIDDEN_BUJJI_SUBPACKAGES = {"broker", "core", "trade", "intelligence", "market", "tick"}


def _module_source_files():
    base = Path(engine.__file__).parent
    return list(base.glob("*.py"))


class TestIsolation:
    def test_no_broker_or_auth_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    lowered = name.lower()
                    for forbidden in (
                        "fyers", "zerodha", "bujji.broker", "bujji.execution",
                        "requests", "websocket", "token", "auth",
                    ):
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

    def test_no_upstream_trading_brain_import(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for forbidden in (
                        "market_state", "risk_brain", "evidence_interpreter",
                        "strategy_selector", "capital_brain", "ontology",
                        "execution_planner", "execution_engine", "position_sizing",
                        "nifty_contract_builder", "order_construction", "broker_adapter",
                    ):
                        assert forbidden not in node.module

    def test_no_auth_or_broker_fields(self):
        fields = {f.name for f in models.RuntimeAuthorization.__dataclass_fields__.values()}
        forbidden = {"access_token", "refresh_token", "auth_token", "broker_order_id", "session_credential"}
        assert not (fields & forbidden)

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_no_network_or_auth_capability_in_source(self):
        for path in _module_source_files():
            source = path.read_text()
            for forbidden in (
                "def authenticate(", "def connect(", "def refresh_token(",
                "def retry(", "def poll(", "def reconcile(", "requests.",
            ):
                assert forbidden not in source
