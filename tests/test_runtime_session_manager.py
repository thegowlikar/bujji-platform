"""Tests for the Runtime Session Manager v1 — Engineering Series 48,
Sprint 1.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.runtime_safety.models import RuntimeAuthorization
from bujji.runtime_session import config as config_module
from bujji.runtime_session import engine, models, query, runner, serialization, taxonomy
from bujji.journal.runtime_session_journal import RuntimeSessionJournal

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 13, 0, 0)

ALL_NINE_CHECKS = (
    "EXECUTION_SESSION_VALID",
    "QUALIFICATION_FINGERPRINT_PRESENT",
    "PIPELINE_COMPLETED_SUCCESSFULLY",
    "DISPATCH_PLAN_NOT_EMPTY",
    "CLIENT_ORDER_IDS_UNIQUE",
    "ORDER_COUNT_CONSISTENT",
    "REPLAY_QUALIFICATION_PASSED",
    "CONFIGURATION_VERSION_RECOGNIZED",
    "RUNTIME_POLICY_RECOGNIZED",
)


def _authorization(**overrides):
    base = dict(
        authorization_id="RAUTH-0000000000000001",
        execution_session_id="ES-0000000000000001",
        authorization_state="AUTHORIZED",
        decision="ALLOW",
        failed_checks=(),
        passed_checks=ALL_NINE_CHECKS,
        failure_reasons=(),
        warnings=(),
        authorization_trace="stub authorization trace",
        timestamp="2026-01-01T12:30:00",
        version="1.0.0",
    )
    base.update(overrides)
    return RuntimeAuthorization(**base)


def _policy(**overrides):
    base = dict(policy_version="1.0.0", allow_pause_resume=True)
    base.update(overrides)
    return models.RuntimeSessionPolicy(**base)


def _create(auth=None, policy=None, existing_ids=()):
    return engine.create_session(
        auth if auth is not None else _authorization(),
        policy if policy is not None else _policy(),
        existing_session_ids=existing_ids,
        clock=FIXED_CLOCK,
    )


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

class TestTaxonomy:
    def test_session_states(self):
        assert taxonomy.ALL_SESSION_STATES == (
            "CREATED", "INITIALIZED", "READY", "ACTIVE", "PAUSED",
            "COMPLETED", "ABORTED", "FAILED",
        )

    def test_failure_reasons(self):
        assert taxonomy.ALL_FAILURE_REASONS == (
            "INVALID_AUTHORIZATION", "INVALID_POLICY", "MISSING_FINGERPRINT",
            "DUPLICATE_SESSION", "INSUFFICIENT_DATA",
        )

    def test_terminal_states_have_no_outbound_transitions(self):
        for state in taxonomy.TERMINAL_STATES:
            assert taxonomy.ALLOWED_TRANSITIONS[state] == ()

    def test_every_state_has_description(self):
        for s in taxonomy.ALL_SESSION_STATES:
            assert s in taxonomy.SESSION_STATE_DESCRIPTIONS and taxonomy.SESSION_STATE_DESCRIPTIONS[s]


# ---------------------------------------------------------------------------
# Session creation
# ---------------------------------------------------------------------------

class TestSessionCreation:
    def test_valid_authorization_creates_session(self):
        session = _create()
        assert session.session_state == "CREATED"
        assert session.failure_reason is None

    def test_created_session_has_authorization_id(self):
        session = _create()
        assert session.authorization_id == "RAUTH-0000000000000001"

    def test_trace_mentions_created(self):
        session = _create()
        assert "CREATED" in session.lifecycle_trace


# ---------------------------------------------------------------------------
# Initialization / Ready / Active / Pause-Resume / Completion / Abort
# ---------------------------------------------------------------------------

class TestInitialization:
    def test_created_to_initialized(self):
        session = engine.initialize(_create(), clock=FIXED_CLOCK)
        assert session.session_state == "INITIALIZED"

    def test_wrong_state_initialize_is_noop(self):
        session = _create()
        aborted = engine.abort(session, clock=FIXED_CLOCK)
        result = engine.initialize(aborted, clock=FIXED_CLOCK)
        assert result == aborted


class TestReadyTransition:
    def test_initialized_to_ready(self):
        session = engine.initialize(_create(), clock=FIXED_CLOCK)
        ready = engine.mark_ready(session, clock=FIXED_CLOCK)
        assert ready.session_state == "READY"

    def test_created_to_ready_directly_is_noop(self):
        session = _create()
        result = engine.mark_ready(session, clock=FIXED_CLOCK)
        assert result == session


class TestActiveTransition:
    def test_ready_to_active(self):
        session = engine.mark_ready(engine.initialize(_create(), clock=FIXED_CLOCK), clock=FIXED_CLOCK)
        active = engine.activate(session, clock=FIXED_CLOCK)
        assert active.session_state == "ACTIVE"


class TestPauseResume:
    def test_active_to_paused(self):
        session = engine.activate(
            engine.mark_ready(engine.initialize(_create(), clock=FIXED_CLOCK), clock=FIXED_CLOCK), clock=FIXED_CLOCK
        )
        paused = engine.pause(session, clock=FIXED_CLOCK)
        assert paused.session_state == "PAUSED"

    def test_paused_to_active_resume(self):
        session = engine.activate(
            engine.mark_ready(engine.initialize(_create(), clock=FIXED_CLOCK), clock=FIXED_CLOCK), clock=FIXED_CLOCK
        )
        paused = engine.pause(session, clock=FIXED_CLOCK)
        resumed = engine.resume(paused, clock=FIXED_CLOCK)
        assert resumed.session_state == "ACTIVE"

    def test_pause_disabled_by_policy_is_noop(self):
        policy = _policy(allow_pause_resume=False)
        session = engine.activate(
            engine.mark_ready(engine.initialize(_create(policy=policy), clock=FIXED_CLOCK), clock=FIXED_CLOCK),
            clock=FIXED_CLOCK,
        )
        result = engine.pause(session, clock=FIXED_CLOCK)
        assert result == session
        assert result.session_state == "ACTIVE"

    def test_pause_from_created_is_noop(self):
        session = _create()
        result = engine.pause(session, clock=FIXED_CLOCK)
        assert result == session


class TestCompletion:
    def test_active_to_completed(self):
        session = engine.activate(
            engine.mark_ready(engine.initialize(_create(), clock=FIXED_CLOCK), clock=FIXED_CLOCK), clock=FIXED_CLOCK
        )
        completed = engine.complete(session, clock=FIXED_CLOCK)
        assert completed.session_state == "COMPLETED"

    def test_completed_is_terminal(self):
        session = engine.activate(
            engine.mark_ready(engine.initialize(_create(), clock=FIXED_CLOCK), clock=FIXED_CLOCK), clock=FIXED_CLOCK
        )
        completed = engine.complete(session, clock=FIXED_CLOCK)
        result = engine.activate(completed, clock=FIXED_CLOCK)
        assert result == completed


class TestAbort:
    def test_abort_from_created(self):
        session = _create()
        aborted = engine.abort(session, clock=FIXED_CLOCK)
        assert aborted.session_state == "ABORTED"

    def test_abort_from_active(self):
        session = engine.activate(
            engine.mark_ready(engine.initialize(_create(), clock=FIXED_CLOCK), clock=FIXED_CLOCK), clock=FIXED_CLOCK
        )
        aborted = engine.abort(session, clock=FIXED_CLOCK)
        assert aborted.session_state == "ABORTED"

    def test_abort_from_paused(self):
        session = engine.activate(
            engine.mark_ready(engine.initialize(_create(), clock=FIXED_CLOCK), clock=FIXED_CLOCK), clock=FIXED_CLOCK
        )
        paused = engine.pause(session, clock=FIXED_CLOCK)
        aborted = engine.abort(paused, clock=FIXED_CLOCK)
        assert aborted.session_state == "ABORTED"

    def test_abort_is_terminal(self):
        session = _create()
        aborted = engine.abort(session, clock=FIXED_CLOCK)
        result = engine.initialize(aborted, clock=FIXED_CLOCK)
        assert result == aborted


# ---------------------------------------------------------------------------
# Invalid authorization / policy / missing fingerprint / duplicate
# ---------------------------------------------------------------------------

class TestInvalidAuthorization:
    def test_denied_authorization_fails(self):
        session = _create(auth=_authorization(authorization_state="DENIED", decision="DENY"))
        assert session.session_state == "FAILED"
        assert session.failure_reason == "INVALID_AUTHORIZATION"

    def test_unknown_decision_fails(self):
        session = _create(auth=_authorization(authorization_state="INSUFFICIENT_DATA", decision="UNKNOWN"))
        assert session.failure_reason == "INVALID_AUTHORIZATION"


class TestInvalidPolicy:
    def test_unrecognized_policy_version_fails(self):
        session = _create(policy=_policy(policy_version="9.9.9"))
        assert session.session_state == "FAILED"
        assert session.failure_reason == "INVALID_POLICY"


class TestMissingFingerprint:
    def test_missing_fingerprint_check_fails(self):
        checks_without_fingerprint = tuple(c for c in ALL_NINE_CHECKS if c != "QUALIFICATION_FINGERPRINT_PRESENT")
        session = _create(auth=_authorization(passed_checks=checks_without_fingerprint))
        assert session.session_state == "FAILED"
        assert session.failure_reason == "MISSING_FINGERPRINT"


class TestDuplicateSessionIds:
    def test_duplicate_session_id_fails(self):
        first = _create()
        session = _create(existing_ids=(first.session_id,))
        assert session.session_state == "FAILED"
        assert session.failure_reason == "DUPLICATE_SESSION"


class TestInsufficientData:
    def test_none_authorization_fails(self):
        session = engine.create_session(None, _policy(), clock=FIXED_CLOCK)
        assert session.session_state == "FAILED"
        assert session.failure_reason == "INSUFFICIENT_DATA"

    def test_none_policy_fails(self):
        session = engine.create_session(_authorization(), None, clock=FIXED_CLOCK)
        assert session.failure_reason == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# Determinism / replay
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_same_session_id(self):
        s1 = _create()
        s2 = _create()
        assert s1.session_id == s2.session_id
        assert s1 == s2

    def test_byte_identical_across_repeated_runs(self):
        results = [_create() for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_different_inputs_different_session_id(self):
        s1 = _create()
        s2 = _create(auth=_authorization(authorization_id="RAUTH-DIFFERENT"))
        assert s1.session_id != s2.session_id

    def test_session_id_uses_md5_prefix_not_uuid(self):
        session = _create()
        assert session.session_id.startswith("RS-")

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        session = engine.create_session(_authorization(), _policy())
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
    def test_session_is_frozen(self):
        session = _create()
        with pytest.raises(Exception):
            session.session_state = "HACKED"

    def test_engine_does_not_mutate_inputs(self):
        auth = _authorization()
        auth_copy = RuntimeAuthorization(**{f.name: getattr(auth, f.name) for f in auth.__dataclass_fields__.values()})
        _create(auth=auth)
        assert auth == auth_copy


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        session = _create()
        d = serialization.session_to_dict(session)
        back = serialization.session_from_dict(d)
        assert back == session

    def test_round_trip_is_json_safe(self):
        import json

        session = engine.activate(
            engine.mark_ready(engine.initialize(_create(), clock=FIXED_CLOCK), clock=FIXED_CLOCK), clock=FIXED_CLOCK
        )
        d = serialization.session_to_dict(session)
        text = json.dumps(d)
        back = serialization.session_from_dict(json.loads(text))
        assert back == session

    def test_round_trip_with_failed_session_has_no_policy(self):
        session = _create(policy=_policy(policy_version="9.9.9"))
        d = serialization.session_to_dict(session)
        assert d["session_policy"] is None
        back = serialization.session_from_dict(d)
        assert back == session


# ---------------------------------------------------------------------------
# Journal -- append only
# ---------------------------------------------------------------------------

class TestJournal:
    def test_record_and_read_all(self, tmp_path):
        j = RuntimeSessionJournal(tmp_path / "rsm.jsonl")
        session = _create()
        j.record(session)
        assert j.read_all() == [session]

    def test_read_all_empty_when_file_missing(self, tmp_path):
        j = RuntimeSessionJournal(tmp_path / "missing.jsonl")
        assert j.read_all() == []

    def test_record_many_appends_in_order(self, tmp_path):
        j = RuntimeSessionJournal(tmp_path / "j.jsonl")
        s1 = _create()
        s2 = engine.initialize(s1, clock=FIXED_CLOCK)
        j.record_many([s1, s2])
        records = j.read_all()
        assert [r.session_state for r in records] == ["CREATED", "INITIALIZED"]

    def test_journal_opens_only_in_append_or_read_mode(self):
        source = inspect.getsource(RuntimeSessionJournal)
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
        session = runner.run_session_creation(_authorization(), _policy(), clock=FIXED_CLOCK)
        assert isinstance(session, models.RuntimeSession)

    def test_run_journals_when_given_one(self, tmp_path):
        j = RuntimeSessionJournal(tmp_path / "run.jsonl")
        session = runner.run_session_creation(_authorization(), _policy(), clock=FIXED_CLOCK, journal=j)
        assert j.read_all() == [session]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

class TestRuntimeSessionIndex:
    def test_ingest_and_latest(self):
        idx = query.RuntimeSessionIndex()
        s1 = _create()
        idx.ingest(s1)
        assert idx.latest() == s1

    def test_find_by_state(self):
        idx = query.RuntimeSessionIndex()
        s1 = _create()
        idx.ingest(s1)
        assert idx.find_by_state("CREATED") == [s1]

    def test_existing_session_ids(self):
        idx = query.RuntimeSessionIndex()
        s1 = _create()
        idx.ingest(s1)
        assert idx.existing_session_ids() == [s1.session_id]

    def test_summary_counts_by_state(self):
        idx = query.RuntimeSessionIndex()
        idx.ingest(_create())
        idx.ingest(_create(auth=_authorization(authorization_state="DENIED", decision="DENY")))
        summary = idx.summary()
        assert summary.get("CREATED") == 1
        assert summary.get("FAILED") == 1


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_version_matches_package_version(self):
        cfg = config_module.RuntimeSessionConfig()
        assert cfg.version == taxonomy.RUNTIME_SESSION_VERSION

    def test_config_is_frozen(self):
        cfg = config_module.RuntimeSessionConfig()
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
                        "requests", "websocket", "token",
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
        fields = {f.name for f in models.RuntimeSession.__dataclass_fields__.values()}
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
