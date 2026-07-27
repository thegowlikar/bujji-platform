"""Tests for the Runtime Recovery Coordinator — Engineering Series 53,
Sprint 1.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.runtime import recovery_coordinator as rc

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 16, 0, 0)


class _StubSource:
    def __init__(self, snapshot: rc.ProductionRecoverySnapshot):
        self._snapshot = snapshot

    def get_recovery_snapshot(self) -> rc.ProductionRecoverySnapshot:
        return self._snapshot


def _snapshot(**overrides):
    base = dict(
        is_healthy=True,
        broker_connected=True,
        has_open_position=False,
        position_state="WAITING",
        recovery_error=None,
    )
    base.update(overrides)
    return rc.ProductionRecoverySnapshot(**base)


# ---------------------------------------------------------------------------
# Clean restart
# ---------------------------------------------------------------------------

class TestCleanRestart:
    def test_clean_slate_reconstructs_ready_runtime_session(self):
        source = _StubSource(_snapshot(position_state="WAITING", has_open_position=False))
        result = rc.recover(source, clock=FIXED_CLOCK)
        assert result.recovery_status == "RECOVERED"
        assert result.runtime_session.session_state == "READY"
        assert result.broker_session.authentication_state == "AUTHENTICATED"
        assert result.broker_session.session_state == "READY"

    def test_clean_restart_execution_session_gap_reason_notes_no_position(self):
        source = _StubSource(_snapshot(has_open_position=False))
        result = rc.recover(source, clock=FIXED_CLOCK)
        assert result.execution_session is None
        assert "No open position" in result.execution_session_gap_reason


# ---------------------------------------------------------------------------
# Restart with active RuntimeSession (position resumed)
# ---------------------------------------------------------------------------

class TestRestartWithActiveSession:
    def test_in_position_reconstructs_active_runtime_session(self):
        source = _StubSource(_snapshot(position_state="IN_POSITION", has_open_position=True))
        result = rc.recover(source, clock=FIXED_CLOCK)
        assert result.recovery_status == "RECOVERED"
        assert result.runtime_session.session_state == "ACTIVE"

    def test_exiting_also_reconstructs_active(self):
        source = _StubSource(_snapshot(position_state="EXITING", has_open_position=True))
        result = rc.recover(source, clock=FIXED_CLOCK)
        assert result.runtime_session.session_state == "ACTIVE"

    def test_active_session_execution_session_gap_reason_notes_missing_order_data(self):
        source = _StubSource(_snapshot(position_state="IN_POSITION", has_open_position=True))
        result = rc.recover(source, clock=FIXED_CLOCK)
        assert result.execution_session is None
        assert "original OrderRequests" in result.execution_session_gap_reason


# ---------------------------------------------------------------------------
# Restart with completed execution (already flat)
# ---------------------------------------------------------------------------

class TestRestartWithCompletedExecution:
    def test_done_for_day_reconstructs_completed_runtime_session(self):
        source = _StubSource(_snapshot(position_state="DONE_FOR_DAY", has_open_position=False))
        result = rc.recover(source, clock=FIXED_CLOCK)
        assert result.recovery_status == "RECOVERED"
        assert result.runtime_session.session_state == "COMPLETED"

    def test_orphan_flattened_still_recovers_with_unhealthy_noted(self):
        # is_healthy=False (orphan flattened) but recovery itself
        # succeeded -- no recovery_error was raised.
        source = _StubSource(_snapshot(position_state="DONE_FOR_DAY", has_open_position=False, is_healthy=False))
        result = rc.recover(source, clock=FIXED_CLOCK)
        assert result.recovery_status == "RECOVERED"
        assert result.runtime_session.session_state == "COMPLETED"


# ---------------------------------------------------------------------------
# Restart with failed production recovery
# ---------------------------------------------------------------------------

class TestRestartWithFailedProductionRecovery:
    def test_recovery_error_yields_failed(self):
        source = _StubSource(_snapshot(recovery_error="FYERS credentials missing", broker_connected=False))
        result = rc.recover(source, clock=FIXED_CLOCK)
        assert result.recovery_status == "FAILED"
        assert result.failure_reason == "FYERS credentials missing"
        assert result.runtime_session is None
        assert result.broker_session is None

    def test_failure_never_fabricates_sessions(self):
        source = _StubSource(_snapshot(recovery_error="auth token expired"))
        result = rc.recover(source, clock=FIXED_CLOCK)
        assert result.runtime_session is None
        assert result.broker_session is None
        assert result.execution_session is None

    def test_broker_not_connected_without_error_is_insufficient_data(self):
        source = _StubSource(_snapshot(broker_connected=False, recovery_error=None))
        result = rc.recover(source, clock=FIXED_CLOCK)
        assert result.recovery_status == "INSUFFICIENT_DATA"

    def test_unrecognized_position_state_is_insufficient_data(self):
        source = _StubSource(_snapshot(position_state="SOME_UNKNOWN_STATE"))
        result = rc.recover(source, clock=FIXED_CLOCK)
        assert result.recovery_status == "INSUFFICIENT_DATA"

    def test_none_position_state_is_insufficient_data(self):
        source = _StubSource(_snapshot(position_state=None))
        result = rc.recover(source, clock=FIXED_CLOCK)
        assert result.recovery_status == "INSUFFICIENT_DATA"

    def test_none_source_is_insufficient_data(self):
        result = rc.recover(None, clock=FIXED_CLOCK)
        assert result.recovery_status == "INSUFFICIENT_DATA"
        assert result.runtime_session is None


# ---------------------------------------------------------------------------
# Runtime reconstruction correctness
# ---------------------------------------------------------------------------

class TestRuntimeReconstructionCorrectness:
    def test_broker_session_linked_to_runtime_session(self):
        source = _StubSource(_snapshot())
        result = rc.recover(source, clock=FIXED_CLOCK)
        assert result.broker_session.runtime_session_id == result.runtime_session.session_id

    def test_runtime_session_has_no_authorization_id(self):
        # Recovery reconstructs continuity; it never repeats a fresh
        # authorization decision.
        source = _StubSource(_snapshot())
        result = rc.recover(source, clock=FIXED_CLOCK)
        assert result.runtime_session.authorization_id is None

    def test_only_recognized_states_ever_used(self):
        from bujji.runtime_session import taxonomy as rs_taxonomy
        from bujji.authentication import taxonomy as auth_taxonomy

        source = _StubSource(_snapshot(position_state="IN_POSITION", has_open_position=True))
        result = rc.recover(source, clock=FIXED_CLOCK)
        assert result.runtime_session.session_state in rs_taxonomy.ALL_SESSION_STATES
        assert result.broker_session.authentication_state in auth_taxonomy.ALL_AUTHENTICATION_STATES
        assert result.broker_session.session_state in auth_taxonomy.ALL_BROKER_SESSION_STATES


# ---------------------------------------------------------------------------
# No order recreation / no position recreation / no retry / no
# authentication / no broker logic
# ---------------------------------------------------------------------------

class TestNoOrderOrPositionRecreation:
    def test_execution_session_never_constructed(self):
        for position_state, has_position in (("WAITING", False), ("IN_POSITION", True), ("DONE_FOR_DAY", False)):
            source = _StubSource(_snapshot(position_state=position_state, has_open_position=has_position))
            result = rc.recover(source, clock=FIXED_CLOCK)
            assert result.execution_session is None

    def test_no_order_request_construction_in_source(self):
        source_code = inspect.getsource(rc)
        assert "OrderRequest(" not in source_code
        assert "NiftyOptionContract(" not in source_code


class TestNoRetryNoAuthNoBrokerLogic:
    def test_no_retry_loop_in_source(self):
        source_code = inspect.getsource(rc).lower()
        for forbidden in ("for attempt", "while true", "retry_attempts", "backoff"):
            assert forbidden not in source_code

    def test_no_authentication_call_in_source(self):
        # This module's own docstring legitimately mentions
        # AuthenticationProviderInterface in prose (comparing this
        # sprint's Protocol pattern to Series 49's) -- restrict the
        # "no authenticate() call" check to actual Call nodes, not a
        # naive whole-source substring scan.
        tree = ast.parse(inspect.getsource(rc))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr != "authenticate"

    def test_no_broker_or_execution_engine_import(self):
        tree = ast.parse(inspect.getsource(rc))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                lowered = name.lower()
                assert "bujji.broker" not in lowered
                assert "bujji.execution" not in lowered
                assert "orchestrator" not in lowered
                assert "fyers" not in lowered

    def test_no_reconcile_or_submit_call(self):
        source_code = inspect.getsource(rc)
        assert ".reconcile(" not in source_code
        assert ".submit_and_confirm(" not in source_code


# ---------------------------------------------------------------------------
# Runtime reconstructed solely from production truth / composition,
# not replacement
# ---------------------------------------------------------------------------

class TestComposesRatherThanReplaces:
    def test_no_orchestrator_recover_call(self):
        # This module's own docstring legitimately discusses
        # Orchestrator._recover() in prose -- restrict the check to
        # actual Call nodes, not a naive whole-source substring scan.
        tree = ast.parse(inspect.getsource(rc))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr != "_recover"

    def test_source_is_computed_purely_from_snapshot_fields(self):
        # Two snapshots differing only in position_state produce
        # different results -- proving reconstruction is driven solely
        # by the snapshot, not by any hidden state.
        r1 = rc.recover(_StubSource(_snapshot(position_state="WAITING")), clock=FIXED_CLOCK)
        r2 = rc.recover(_StubSource(_snapshot(position_state="DONE_FOR_DAY")), clock=FIXED_CLOCK)
        assert r1.runtime_session.session_state != r2.runtime_session.session_state


# ---------------------------------------------------------------------------
# Traceability
# ---------------------------------------------------------------------------

class TestTraceability:
    def test_trace_mentions_restart_detected_and_recovery_invoked(self):
        result = rc.recover(_StubSource(_snapshot()), clock=FIXED_CLOCK)
        assert "Restart detected" in result.recovery_trace
        assert "Production recovery invoked" in result.recovery_trace

    def test_trace_mentions_final_status(self):
        result = rc.recover(_StubSource(_snapshot()), clock=FIXED_CLOCK)
        assert "RECOVERED" in result.recovery_trace

    def test_failure_trace_includes_error(self):
        result = rc.recover(_StubSource(_snapshot(recovery_error="boom")), clock=FIXED_CLOCK)
        assert "boom" in result.recovery_trace


# ---------------------------------------------------------------------------
# Determinism / replay
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_snapshot_same_recovery_id(self):
        r1 = rc.recover(_StubSource(_snapshot()), clock=FIXED_CLOCK)
        r2 = rc.recover(_StubSource(_snapshot()), clock=FIXED_CLOCK)
        assert r1.recovery_id == r2.recovery_id
        assert r1 == r2

    def test_byte_identical_across_repeated_runs(self):
        results = [rc.recover(_StubSource(_snapshot(position_state="IN_POSITION", has_open_position=True)), clock=FIXED_CLOCK) for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_recovery_id_uses_md5_prefix_not_uuid(self):
        result = rc.recover(_StubSource(_snapshot()), clock=FIXED_CLOCK)
        assert result.recovery_id.startswith("RR-")

    def test_real_clock_default_produces_wall_clock_timestamp(self):
        before = datetime.now()
        result = rc.recover(_StubSource(_snapshot()))
        after = datetime.now()
        parsed = datetime.fromisoformat(result.timestamp)
        assert before <= parsed <= after

    def test_no_uuid4_used(self):
        tree = ast.parse(inspect.getsource(rc))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                raise AssertionError("uuid4 used")

    def test_no_randomness_module_used(self):
        source_code = inspect.getsource(rc)
        assert "import random" not in source_code


# ---------------------------------------------------------------------------
# Immutable dataclasses
# ---------------------------------------------------------------------------

class TestFrozen:
    def test_result_is_frozen(self):
        result = rc.recover(_StubSource(_snapshot()), clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            result.recovery_status = "HACKED"

    def test_snapshot_is_frozen(self):
        snap = _snapshot()
        with pytest.raises(Exception):
            snap.is_healthy = False

    def test_reconstructed_runtime_session_is_frozen(self):
        result = rc.recover(_StubSource(_snapshot()), clock=FIXED_CLOCK)
        with pytest.raises(Exception):
            result.runtime_session.session_state = "HACKED"


# ---------------------------------------------------------------------------
# Protocol satisfaction
# ---------------------------------------------------------------------------

class TestProtocolSatisfaction:
    def test_stub_source_satisfies_protocol(self):
        source = _StubSource(_snapshot())
        assert isinstance(source, rc.ProductionRecoverySource)
