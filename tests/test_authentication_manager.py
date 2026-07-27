"""Tests for the Authentication & Broker Session Manager v1 —
Engineering Series 49, Sprint 1.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bujji.runtime_session.models import RuntimeSession, RuntimeSessionPolicy
from bujji.authentication import config as config_module
from bujji.authentication import engine, models, query, runner, serialization, taxonomy
from bujji.journal.authentication_journal import AuthenticationJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 13, 30, 0)


def _runtime_session(**overrides):
    base = dict(
        session_id="RS-0000000000000001",
        authorization_id="RAUTH-0000000000000001",
        session_state="READY",
        session_policy=RuntimeSessionPolicy(policy_version="1.0.0"),
        lifecycle_trace="stub lifecycle trace",
        failure_reason=None,
        created_at="2026-01-01T13:00:00",
        updated_at="2026-01-01T13:00:00",
        version="1.0.0",
    )
    base.update(overrides)
    return RuntimeSession(**base)


def _policy(**overrides):
    base = dict(policy_version="1.0.0", session_ttl_seconds=28800)
    base.update(overrides)
    return models.AuthenticationPolicy(**base)


class _StubProvider:
    def __init__(self, success=True, broker_identity="FYERS:USER123", expires_at=None, error=None):
        self.success = success
        self.broker_identity = broker_identity
        self.expires_at = expires_at
        self.error = error
        self.calls = 0

    def authenticate(self):
        self.calls += 1
        return models.AuthenticationOutcome(
            success=self.success, broker_identity=self.broker_identity,
            expires_at=self.expires_at, error=self.error,
        )


def _create(rs=None, policy=None):
    return engine.create_broker_session(
        rs if rs is not None else _runtime_session(),
        policy if policy is not None else _policy(),
        clock=FIXED_CLOCK,
    )


def _fully_authenticate(provider=None):
    session = _create()
    session = engine.begin_authentication(session, clock=FIXED_CLOCK)
    session = engine.complete_authentication(session, provider or _StubProvider(), _policy(), clock=FIXED_CLOCK)
    return session


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_authentication_states(self):
        assert taxonomy.ALL_AUTHENTICATION_STATES == (
            "UNAUTHENTICATED", "AUTHENTICATING", "AUTHENTICATED", "EXPIRED", "FAILED",
        )

    def test_broker_session_states(self):
        assert taxonomy.ALL_BROKER_SESSION_STATES == (
            "DISCONNECTED", "CONNECTED", "READY", "EXPIRED", "FAILED",
        )

    def test_failure_reasons(self):
        assert taxonomy.ALL_FAILURE_REASONS == (
            "INVALID_RUNTIME_SESSION", "INVALID_POLICY", "AUTHENTICATION_FAILED",
            "SESSION_EXPIRED", "INSUFFICIENT_DATA",
        )

    def test_terminal_states(self):
        assert taxonomy.TERMINAL_AUTH_STATES == ("EXPIRED", "FAILED")
        assert taxonomy.TERMINAL_SESSION_STATES == ("EXPIRED", "FAILED")


# ---------------------------------------------------------------------------
# Successful authentication lifecycle
# ---------------------------------------------------------------------------

class TestSuccessfulAuthenticationLifecycle:
    def test_create_yields_unauthenticated_disconnected(self):
        session = _create()
        assert session.authentication_state == "UNAUTHENTICATED"
        assert session.session_state == "DISCONNECTED"

    def test_begin_authentication_transitions(self):
        session = engine.begin_authentication(_create(), clock=FIXED_CLOCK)
        assert session.authentication_state == "AUTHENTICATING"
        assert session.session_state == "DISCONNECTED"

    def test_complete_authentication_success(self):
        session = _fully_authenticate()
        assert session.authentication_state == "AUTHENTICATED"
        assert session.broker_identity == "FYERS:USER123"
        assert session.expires_at is not None

    def test_full_lifecycle_to_ready(self):
        session = _fully_authenticate()
        session = engine.connect(session, clock=FIXED_CLOCK)
        assert session.session_state == "CONNECTED"
        session = engine.mark_ready(session, clock=FIXED_CLOCK)
        assert session.session_state == "READY"
        assert session.authentication_state == "AUTHENTICATED"

    def test_provider_expires_at_takes_precedence(self):
        custom_expiry = (FIXED_CLOCK() + timedelta(hours=1)).isoformat()
        session = _fully_authenticate(_StubProvider(expires_at=custom_expiry))
        assert session.expires_at == custom_expiry

    def test_provider_called_exactly_once(self):
        provider = _StubProvider()
        _fully_authenticate(provider)
        assert provider.calls == 1


# ---------------------------------------------------------------------------
# Authentication failure
# ---------------------------------------------------------------------------

class TestAuthenticationFailure:
    def test_provider_failure_yields_failed_state(self):
        session = _fully_authenticate(_StubProvider(success=False, broker_identity=None, error="bad credentials"))
        assert session.authentication_state == "FAILED"
        assert session.session_state == "FAILED"
        assert session.failure_reason == "AUTHENTICATION_FAILED"

    def test_failed_session_has_no_broker_identity(self):
        session = _fully_authenticate(_StubProvider(success=False, broker_identity=None))
        assert session.broker_identity is None


# ---------------------------------------------------------------------------
# Session expiry
# ---------------------------------------------------------------------------

class TestSessionExpiry:
    def test_expiry_after_deadline(self):
        past_expiry = (FIXED_CLOCK() - timedelta(seconds=1)).isoformat()
        session = _fully_authenticate(_StubProvider(expires_at=past_expiry))
        expired = engine.check_expiry(session, clock=FIXED_CLOCK)
        assert expired.authentication_state == "EXPIRED"
        assert expired.session_state == "EXPIRED"
        assert expired.failure_reason == "SESSION_EXPIRED"

    def test_no_expiry_before_deadline(self):
        future_expiry = (FIXED_CLOCK() + timedelta(hours=1)).isoformat()
        session = _fully_authenticate(_StubProvider(expires_at=future_expiry))
        result = engine.check_expiry(session, clock=FIXED_CLOCK)
        assert result.authentication_state == "AUTHENTICATED"

    def test_check_expiry_noop_when_not_authenticated(self):
        session = _create()
        result = engine.check_expiry(session, clock=FIXED_CLOCK)
        assert result == session


# ---------------------------------------------------------------------------
# Invalid runtime session / invalid policy
# ---------------------------------------------------------------------------

class TestInvalidRuntimeSession:
    def test_non_ready_active_session_fails(self):
        session = _create(rs=_runtime_session(session_state="CREATED"))
        assert session.authentication_state == "FAILED"
        assert session.failure_reason == "INVALID_RUNTIME_SESSION"

    def test_active_session_is_valid(self):
        session = _create(rs=_runtime_session(session_state="ACTIVE"))
        assert session.authentication_state == "UNAUTHENTICATED"


class TestInvalidPolicy:
    def test_unrecognized_policy_version_fails(self):
        session = _create(policy=_policy(policy_version="9.9.9"))
        assert session.authentication_state == "FAILED"
        assert session.failure_reason == "INVALID_POLICY"


class TestInsufficientData:
    def test_none_runtime_session_fails(self):
        session = engine.create_broker_session(None, _policy(), clock=FIXED_CLOCK)
        assert session.failure_reason == "INSUFFICIENT_DATA"

    def test_none_policy_fails(self):
        session = engine.create_broker_session(_runtime_session(), None, clock=FIXED_CLOCK)
        assert session.failure_reason == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# Determinism / replay
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_broker_session_id(self):
        s1 = _create()
        s2 = _create()
        assert s1.broker_session_id == s2.broker_session_id
        assert s1 == s2

    def test_byte_identical_across_repeated_runs(self):
        results = [_fully_authenticate() for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_different_inputs_different_broker_session_id(self):
        s1 = _create()
        s2 = _create(rs=_runtime_session(session_id="RS-DIFFERENT"))
        assert s1.broker_session_id != s2.broker_session_id

    def test_broker_session_id_uses_md5_prefix_not_uuid(self):
        session = _create()
        assert session.broker_session_id.startswith("BS-")

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        session = engine.create_broker_session(_runtime_session(), _policy())
        after = datetime.now()
        parsed = datetime.fromisoformat(session.created_at)
        assert before <= parsed <= after

    def test_no_randomness_module_used(self):
        source = inspect.getsource(engine)
        assert "import random" not in source
        assert "random." not in source


# ---------------------------------------------------------------------------
# Immutable dataclasses
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_broker_session_is_frozen(self):
        session = _create()
        with pytest.raises(Exception):
            session.authentication_state = "HACKED"

    def test_engine_does_not_mutate_inputs(self):
        rs = _runtime_session()
        rs_copy = RuntimeSession(**{f.name: getattr(rs, f.name) for f in rs.__dataclass_fields__.values()})
        _create(rs=rs)
        assert rs == rs_copy


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        session = _fully_authenticate()
        d = serialization.broker_session_to_dict(session)
        back = serialization.broker_session_from_dict(d)
        assert back == session

    def test_round_trip_is_json_safe(self):
        import json

        session = _fully_authenticate()
        d = serialization.broker_session_to_dict(session)
        text = json.dumps(d)
        back = serialization.broker_session_from_dict(json.loads(text))
        assert back == session

    def test_authentication_policy_round_trip(self):
        p = _policy()
        d = serialization.authentication_policy_to_dict(p)
        assert serialization.authentication_policy_from_dict(d) == p

    def test_authentication_outcome_round_trip(self):
        o = models.AuthenticationOutcome(success=True, broker_identity="X", expires_at="2026-01-02T00:00:00")
        d = serialization.authentication_outcome_to_dict(o)
        assert serialization.authentication_outcome_from_dict(d) == o


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = AuthenticationJournal(tmp_path / "auth.jsonl")
        session = _create()
        j.record(session)
        assert j.read_all() == [session]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = AuthenticationJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = AuthenticationJournal(tmp_path / "j.jsonl")
        s1 = _create()
        s2 = engine.begin_authentication(s1, clock=FIXED_CLOCK)
        j.record_many([s1, s2])
        records = j.read_all()
        assert [r.authentication_state for r in records] == ["UNAUTHENTICATED", "AUTHENTICATING"]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(AuthenticationJournal)
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
        session = runner.run_broker_session_creation(_runtime_session(), _policy(), clock=FIXED_CLOCK)
        assert isinstance(session, models.BrokerSession)

    def test_run_journals_when_given_one(self, tmp_path):
        j = AuthenticationJournal(tmp_path / "run.jsonl")
        session = runner.run_broker_session_creation(_runtime_session(), _policy(), clock=FIXED_CLOCK, journal=j)
        assert j.read_all() == [session]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestBrokerSessionIndex:
    def test_ingest_and_latest(self):
        idx = query.BrokerSessionIndex()
        s1 = _create()
        idx.ingest(s1)
        assert idx.latest() == s1

    def test_find_by_authentication_state(self):
        idx = query.BrokerSessionIndex()
        s1 = _create()
        idx.ingest(s1)
        assert idx.find_by_authentication_state("UNAUTHENTICATED") == [s1]

    def test_find_by_session_state(self):
        idx = query.BrokerSessionIndex()
        s1 = _create()
        idx.ingest(s1)
        assert idx.find_by_session_state("DISCONNECTED") == [s1]

    def test_summary_counts_by_authentication_state(self):
        idx = query.BrokerSessionIndex()
        idx.ingest(_create())
        idx.ingest(_create(policy=_policy(policy_version="9.9.9")))
        summary = idx.summary()
        assert summary.get("UNAUTHENTICATED") == 1
        assert summary.get("FAILED") == 1


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_version_matches_package_version(self):
        cfg = config_module.AuthenticationManagerConfig()
        assert cfg.version == taxonomy.AUTHENTICATION_MANAGER_VERSION

    def test_config_is_frozen(self):
        cfg = config_module.AuthenticationManagerConfig()
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
    def test_no_broker_sdk_import(self):
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
                        "requests", "websocket",
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
                        "runtime_execution", "runtime_safety",
                    ):
                        assert forbidden not in node.module

    def test_no_order_or_fill_or_reconciliation_fields(self):
        fields = {f.name for f in models.BrokerSession.__dataclass_fields__.values()}
        forbidden = {"order_id", "fill_price", "reconciled", "retry_count", "position"}
        assert not (fields & forbidden)

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_no_order_submission_or_reconciliation_capability_in_source(self):
        for path in _module_source_files():
            source = path.read_text()
            for forbidden in (
                "place_order(", "def submit_order(", "def poll_fills(",
                "def reconcile(", "def retry(", "requests.",
            ):
                assert forbidden not in source

    def test_authentication_provider_interface_is_runtime_checkable(self):
        class _Stub:
            def authenticate(self):
                return models.AuthenticationOutcome(success=True)

        assert isinstance(_Stub(), engine.AuthenticationProviderInterface)
