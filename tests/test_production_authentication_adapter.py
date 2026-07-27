"""Tests for the Production Authentication Adapter — Engineering
Series 51, Sprint 1.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from bujji.authentication import models
from bujji.authentication.engine import AuthenticationProviderInterface
from bujji.broker.errors import AuthenticationError
from bujji.integration import authentication_adapter as adapter_module
from bujji.integration.authentication_adapter import ProductionAuthenticationAdapter


class _StubBroker:
    """Satisfies the same narrow shape as production's real
    FyersBroker: an async connect() that raises AuthenticationError on
    failure and returns normally on success. Never imports the FYERS
    SDK, never opens a socket.
    """

    def __init__(self, should_fail=False, unexpected_error=False):
        self.should_fail = should_fail
        self.unexpected_error = unexpected_error
        self.connect_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.unexpected_error:
            raise RuntimeError("simulated unexpected production error")
        if self.should_fail:
            raise AuthenticationError("simulated FYERS auth failure: token invalid")
        return None


# ---------------------------------------------------------------------------
# Successful authentication
# ---------------------------------------------------------------------------

class TestSuccessfulAuthentication:
    def test_connect_success_yields_authenticated_outcome(self):
        broker = _StubBroker()
        adapter = ProductionAuthenticationAdapter(broker, broker_identity="FYERS:USER123")
        outcome = adapter.authenticate()
        assert outcome.success is True
        assert outcome.broker_identity == "FYERS:USER123"
        assert outcome.error is None

    def test_expires_at_is_none_this_sprint(self):
        adapter = ProductionAuthenticationAdapter(_StubBroker())
        outcome = adapter.authenticate()
        assert outcome.expires_at is None

    def test_connect_called_exactly_once(self):
        broker = _StubBroker()
        adapter = ProductionAuthenticationAdapter(broker)
        adapter.authenticate()
        assert broker.connect_calls == 1


# ---------------------------------------------------------------------------
# Production failure
# ---------------------------------------------------------------------------

class TestProductionFailure:
    def test_authentication_error_yields_failed_outcome(self):
        broker = _StubBroker(should_fail=True)
        adapter = ProductionAuthenticationAdapter(broker)
        outcome = adapter.authenticate()
        assert outcome.success is False
        assert outcome.broker_identity is None
        assert "simulated FYERS auth failure" in outcome.error

    def test_unexpected_exception_never_suppressed_never_reraised(self):
        broker = _StubBroker(unexpected_error=True)
        adapter = ProductionAuthenticationAdapter(broker)
        outcome = adapter.authenticate()  # must not raise
        assert outcome.success is False
        assert "simulated unexpected production error" in outcome.error

    def test_failure_never_calls_connect_twice(self):
        broker = _StubBroker(should_fail=True)
        adapter = ProductionAuthenticationAdapter(broker)
        adapter.authenticate()
        assert broker.connect_calls == 1


# ---------------------------------------------------------------------------
# Translation correctness
# ---------------------------------------------------------------------------

class TestTranslationCorrectness:
    def test_only_authentication_outcome_type_returned(self):
        adapter = ProductionAuthenticationAdapter(_StubBroker())
        outcome = adapter.authenticate()
        assert isinstance(outcome, models.AuthenticationOutcome)

    def test_failure_outcome_is_also_authentication_outcome(self):
        adapter = ProductionAuthenticationAdapter(_StubBroker(should_fail=True))
        outcome = adapter.authenticate()
        assert isinstance(outcome, models.AuthenticationOutcome)


# ---------------------------------------------------------------------------
# Deterministic traces
# ---------------------------------------------------------------------------

class TestDeterministicTraces:
    def test_trace_records_all_four_elements_on_success(self):
        adapter = ProductionAuthenticationAdapter(_StubBroker())
        adapter.authenticate()
        trace = adapter.last_trace
        assert "Runtime request" in trace
        assert "Production method invoked" in trace
        assert "Production response" in trace
        assert "AuthenticationOutcome" in trace

    def test_trace_records_production_error_verbatim(self):
        adapter = ProductionAuthenticationAdapter(_StubBroker(should_fail=True))
        adapter.authenticate()
        assert "simulated FYERS auth failure" in adapter.last_trace

    def test_repeated_calls_with_same_stub_produce_same_trace_shape(self):
        adapter1 = ProductionAuthenticationAdapter(_StubBroker())
        adapter2 = ProductionAuthenticationAdapter(_StubBroker())
        adapter1.authenticate()
        adapter2.authenticate()
        assert adapter1.last_trace == adapter2.last_trace


# ---------------------------------------------------------------------------
# Immutable outputs
# ---------------------------------------------------------------------------

class TestImmutableOutputs:
    def test_outcome_is_frozen(self):
        adapter = ProductionAuthenticationAdapter(_StubBroker())
        outcome = adapter.authenticate()
        with pytest.raises(Exception):
            outcome.success = False


# ---------------------------------------------------------------------------
# No retry / no token refresh / no duplicated authentication logic
# ---------------------------------------------------------------------------

class TestNoRetry:
    def test_adapter_never_retries_on_failure(self):
        broker = _StubBroker(should_fail=True)
        adapter = ProductionAuthenticationAdapter(broker)
        adapter.authenticate()
        assert broker.connect_calls == 1

    def test_no_retry_loop_in_source(self):
        source = inspect.getsource(adapter_module)
        for forbidden in ("for attempt", "while True", "retry_attempts", "backoff"):
            assert forbidden not in source.lower()


class TestNoTokenRefreshLogic:
    def test_no_refresh_method_defined(self):
        assert not hasattr(ProductionAuthenticationAdapter, "refresh")

    def test_no_token_manager_import(self):
        # Restricted to actual import statements -- this module's own
        # docstrings and trace strings legitimately mention
        # FyersTokenManager in prose (explaining it's called
        # internally by FyersBroker.connect()), which a naive
        # substring scan over the whole source would false-positive
        # on.
        tree = ast.parse(inspect.getsource(adapter_module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "fyers_token_manager" not in alias.name.lower()
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert "fyers_token_manager" not in node.module.lower()

    def test_no_credential_persistence_capability(self):
        source = inspect.getsource(adapter_module)
        for forbidden in ("def _persist(", "credentials_file", "os.replace", "open("):
            assert forbidden not in source


class TestNoDuplicatedAuthenticationLogic:
    def test_no_fyers_sdk_or_broker_class_import(self):
        tree = ast.parse(inspect.getsource(adapter_module))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                lowered = name.lower()
                assert "fyers_apiv3" not in lowered
                assert "broker.fyers" not in lowered

    def test_no_expiry_calculation_in_source(self):
        source = inspect.getsource(adapter_module)
        assert "timedelta" not in source
        assert "session_ttl_seconds" not in source


# ---------------------------------------------------------------------------
# Protocol satisfaction
# ---------------------------------------------------------------------------

class TestProtocolSatisfaction:
    def test_adapter_satisfies_authentication_provider_interface(self):
        adapter = ProductionAuthenticationAdapter(_StubBroker())
        assert isinstance(adapter, AuthenticationProviderInterface)

    def test_adapter_usable_with_authentication_engine_complete_authentication(self):
        from bujji.authentication import engine as auth_engine
        from bujji.authentication.models import AuthenticationPolicy
        from bujji.runtime_session.models import RuntimeSession, RuntimeSessionPolicy
        from datetime import datetime

        clock = lambda: datetime(2026, 1, 1, 14, 0, 0)
        runtime_session = RuntimeSession(
            session_id="RS-TEST", authorization_id="RAUTH-TEST", session_state="READY",
            session_policy=RuntimeSessionPolicy(policy_version="1.0.0"),
            lifecycle_trace="x", failure_reason=None,
            created_at="2026-01-01T13:00:00", updated_at="2026-01-01T13:00:00", version="1.0.0",
        )
        policy = AuthenticationPolicy(policy_version="1.0.0")
        broker_session = auth_engine.create_broker_session(runtime_session, policy, clock=clock)
        broker_session = auth_engine.begin_authentication(broker_session, clock=clock)

        adapter = ProductionAuthenticationAdapter(_StubBroker(), broker_identity="FYERS:USER123")
        result = auth_engine.complete_authentication(broker_session, adapter, policy, clock=clock)
        assert result.authentication_state == "AUTHENTICATED"
        assert result.broker_identity == "FYERS:USER123"


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

class TestIsolation:
    def test_no_network_import(self):
        source = inspect.getsource(adapter_module)
        for forbidden in ("import requests", "import websocket", "import socket"):
            assert forbidden not in source

    def test_no_broker_business_logic_terms(self):
        source = inspect.getsource(adapter_module).lower()
        for forbidden in ("place_order", "cancel_order", "get_open_positions", "resolve_atm_contract"):
            assert forbidden not in source
