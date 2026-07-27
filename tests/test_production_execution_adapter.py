"""Tests for the Production Execution Adapter — Engineering Series
52, Sprint 1.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from bujji.core.enums import OrderStatus
from bujji.core.models import OrderResult as ProductionOrderResult
from bujji.execution.engine import ExecutionError
from bujji.runtime_execution.engine import ExecutionEngineInterface
from bujji.trading_brain.nifty_contract_builder.models import NiftyOptionContract
from bujji.trading_brain.order_construction.models import OrderRequest, OrderTags
from bujji.integration import execution_adapter as adapter_module
from bujji.integration.execution_adapter import ExecutionResult, ProductionExecutionAdapter

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 15, 0, 0)


def _runtime_order(client_order_id="COID-1", strike=25150, option_type="CE", side="SELL"):
    contract = NiftyOptionContract(
        contract_id=f"NC-{strike}{option_type}", underlying="NIFTY", expiry="2026-07-31",
        strike=strike, option_type=option_type, side=side,
        contract_symbol=f"NSE:NIFTY{strike}{option_type}", capital_intent="STANDARD",
        strategy_id="PREMIUM_VWAP_STRADDLE", selection_reason="x", construction_trace="x",
        timestamp="2026-01-01T09:00:00", version="1.0.0",
    )
    tags = OrderTags(strategy_id="PREMIUM_VWAP_STRADDLE", session_id="SESSION-1", pipeline_version="1.0.0", qualification_fingerprint="FP-abc")
    return OrderRequest(
        request_id="OR-1", contract=contract, side=side, quantity=75, order_type="MARKET",
        product="MIS", validity="DAY", execution_policy="MARKET", client_order_id=client_order_id,
        tags=tags, creation_trace="x", timestamp="2026-01-01T09:30:00", version="1.0.0",
    )


class _StubExecutionEngine:
    """Satisfies the same narrow shape as production's real
    ExecutionEngine: an async submit_and_confirm(request) that either
    returns a production OrderResult or raises ExecutionError. Never
    imports a broker SDK, never opens a socket.
    """

    def __init__(self, result=None, raise_execution_error=None, raise_unexpected=None):
        self.result = result
        self.raise_execution_error = raise_execution_error
        self.raise_unexpected = raise_unexpected
        self.calls = []

    async def submit_and_confirm(self, request):
        self.calls.append(request)
        if self.raise_unexpected:
            raise self.raise_unexpected
        if self.raise_execution_error:
            raise self.raise_execution_error
        return self.result


def _filled_result(client_order_id="COID-1"):
    return ProductionOrderResult(
        client_order_id=client_order_id, status=OrderStatus.FILLED, broker_order_id="BROKER-1",
        filled_quantity=75, average_price=123.45, message="ok", raw={},
    )


# ---------------------------------------------------------------------------
# Successful execution translation
# ---------------------------------------------------------------------------

class TestSuccessfulExecutionTranslation:
    def test_filled_result_translated(self):
        engine = _StubExecutionEngine(result=_filled_result())
        adapter = ProductionExecutionAdapter(engine, lot_size=75, clock=FIXED_CLOCK)
        outcome = adapter.submit_and_confirm(_runtime_order())
        assert outcome.status == "FILLED"
        assert outcome.broker_order_id == "BROKER-1"
        assert outcome.filled_quantity == 75
        assert outcome.average_price == 123.45

    def test_client_order_id_preserved(self):
        engine = _StubExecutionEngine(result=_filled_result("COID-XYZ"))
        adapter = ProductionExecutionAdapter(engine, lot_size=75, clock=FIXED_CLOCK)
        outcome = adapter.submit_and_confirm(_runtime_order(client_order_id="COID-XYZ"))
        assert outcome.client_order_id == "COID-XYZ"

    def test_partial_status_translated_verbatim(self):
        partial = ProductionOrderResult(
            client_order_id="COID-1", status=OrderStatus.PARTIAL, broker_order_id="B1",
            filled_quantity=25, average_price=100.0, message="partial", raw={},
        )
        engine = _StubExecutionEngine(result=partial)
        adapter = ProductionExecutionAdapter(engine, lot_size=75, clock=FIXED_CLOCK)
        outcome = adapter.submit_and_confirm(_runtime_order())
        assert outcome.status == "PARTIAL"

    def test_translation_calls_engine_exactly_once(self):
        engine = _StubExecutionEngine(result=_filled_result())
        adapter = ProductionExecutionAdapter(engine, lot_size=75, clock=FIXED_CLOCK)
        adapter.submit_and_confirm(_runtime_order())
        assert len(engine.calls) == 1

    def test_production_order_request_fields_correct(self):
        engine = _StubExecutionEngine(result=_filled_result())
        adapter = ProductionExecutionAdapter(engine, lot_size=75, clock=FIXED_CLOCK)
        adapter.submit_and_confirm(_runtime_order())
        production_order = engine.calls[0]
        assert production_order.contract.strike == 25150
        assert production_order.contract.option_type.value == "CE"
        assert production_order.contract.lot_size == 75
        assert production_order.side.value == "SELL"
        assert production_order.quantity == 75
        assert production_order.client_order_id == "COID-1"
        assert production_order.limit_price is None


# ---------------------------------------------------------------------------
# Production failure translation
# ---------------------------------------------------------------------------

class TestProductionFailureTranslation:
    def test_execution_error_translated_to_failed(self):
        engine = _StubExecutionEngine(raise_execution_error=ExecutionError("zero fill after retries"))
        adapter = ProductionExecutionAdapter(engine, lot_size=75, clock=FIXED_CLOCK)
        outcome = adapter.submit_and_confirm(_runtime_order())
        assert outcome.status == "FAILED"
        assert "zero fill after retries" in outcome.message

    def test_failed_status_is_never_a_production_order_status_member(self):
        assert "FAILED" not in [s.value for s in OrderStatus]

    def test_unexpected_exception_never_suppressed(self):
        engine = _StubExecutionEngine(raise_unexpected=RuntimeError("boom"))
        adapter = ProductionExecutionAdapter(engine, lot_size=75, clock=FIXED_CLOCK)
        outcome = adapter.submit_and_confirm(_runtime_order())  # must not raise
        assert outcome.status == "FAILED"
        assert "boom" in outcome.message

    def test_failure_never_retries(self):
        engine = _StubExecutionEngine(raise_execution_error=ExecutionError("zero fill"))
        adapter = ProductionExecutionAdapter(engine, lot_size=75, clock=FIXED_CLOCK)
        adapter.submit_and_confirm(_runtime_order())
        assert len(engine.calls) == 1


# ---------------------------------------------------------------------------
# Runtime interface compliance
# ---------------------------------------------------------------------------

class TestRuntimeInterfaceCompliance:
    def test_adapter_satisfies_execution_engine_interface(self):
        adapter = ProductionExecutionAdapter(_StubExecutionEngine(result=_filled_result()), lot_size=75)
        assert isinstance(adapter, ExecutionEngineInterface)

    def test_adapter_usable_with_runtime_execution_dispatch(self):
        from bujji.runtime_execution import engine as re_engine

        order = _runtime_order()
        session = re_engine.build_session((order,), clock=FIXED_CLOCK)
        queued = re_engine.queue_for_dispatch(session, clock=FIXED_CLOCK)
        adapter = ProductionExecutionAdapter(_StubExecutionEngine(result=_filled_result()), lot_size=75, clock=FIXED_CLOCK)
        dispatched = re_engine.dispatch(queued, adapter, clock=FIXED_CLOCK)
        assert dispatched.execution_state == "DISPATCHED"

    def test_dispatch_aborts_when_adapter_translates_failure_as_exception(self):
        # dispatch() only aborts if the injected executor itself
        # raises -- confirms the Runtime Execution Orchestrator's own
        # contract (Series 45) is unaffected by this adapter existing.
        from bujji.runtime_execution import engine as re_engine

        order = _runtime_order()
        session = re_engine.build_session((order,), clock=FIXED_CLOCK)
        queued = re_engine.queue_for_dispatch(session, clock=FIXED_CLOCK)

        class _RaisingAdapter:
            def submit_and_confirm(self, order_request):
                raise RuntimeError("adapter itself raised")

        result = re_engine.dispatch(queued, _RaisingAdapter(), clock=FIXED_CLOCK)
        assert result.execution_state == "ABORTED"


# ---------------------------------------------------------------------------
# Deterministic traces
# ---------------------------------------------------------------------------

class TestDeterministicTraces:
    def test_trace_records_all_four_elements_on_success(self):
        engine = _StubExecutionEngine(result=_filled_result())
        adapter = ProductionExecutionAdapter(engine, lot_size=75, clock=FIXED_CLOCK)
        adapter.submit_and_confirm(_runtime_order())
        trace = adapter.last_trace
        assert "Runtime request" in trace
        assert "Production method invoked" in trace
        assert "Production response" in trace
        assert "ExecutionResult" in trace

    def test_trace_records_production_error_verbatim(self):
        engine = _StubExecutionEngine(raise_execution_error=ExecutionError("zero fill"))
        adapter = ProductionExecutionAdapter(engine, lot_size=75, clock=FIXED_CLOCK)
        adapter.submit_and_confirm(_runtime_order())
        assert "zero fill" in adapter.last_trace

    def test_repeated_calls_produce_same_trace_shape(self):
        adapter1 = ProductionExecutionAdapter(_StubExecutionEngine(result=_filled_result()), lot_size=75, clock=FIXED_CLOCK)
        adapter2 = ProductionExecutionAdapter(_StubExecutionEngine(result=_filled_result()), lot_size=75, clock=FIXED_CLOCK)
        adapter1.submit_and_confirm(_runtime_order())
        adapter2.submit_and_confirm(_runtime_order())
        assert adapter1.last_trace == adapter2.last_trace


# ---------------------------------------------------------------------------
# Immutable outputs
# ---------------------------------------------------------------------------

class TestImmutableOutputs:
    def test_execution_result_is_frozen(self):
        engine = _StubExecutionEngine(result=_filled_result())
        adapter = ProductionExecutionAdapter(engine, lot_size=75, clock=FIXED_CLOCK)
        outcome = adapter.submit_and_confirm(_runtime_order())
        with pytest.raises(Exception):
            outcome.status = "HACKED"

    def test_only_execution_result_type_returned(self):
        engine = _StubExecutionEngine(result=_filled_result())
        adapter = ProductionExecutionAdapter(engine, lot_size=75, clock=FIXED_CLOCK)
        outcome = adapter.submit_and_confirm(_runtime_order())
        assert isinstance(outcome, ExecutionResult)


# ---------------------------------------------------------------------------
# No retry / no reconciliation / no broker logic
# ---------------------------------------------------------------------------

class TestNoRetry:
    def test_no_retry_on_failure(self):
        engine = _StubExecutionEngine(raise_execution_error=ExecutionError("zero fill"))
        adapter = ProductionExecutionAdapter(engine, lot_size=75, clock=FIXED_CLOCK)
        adapter.submit_and_confirm(_runtime_order())
        assert len(engine.calls) == 1

    def test_no_retry_loop_in_source(self):
        source = inspect.getsource(adapter_module).lower()
        for forbidden in ("for attempt", "while true", "retry_attempts", "backoff"):
            assert forbidden not in source


class TestNoReconciliation:
    def test_no_reconcile_call_in_source(self):
        source = inspect.getsource(adapter_module)
        assert ".reconcile(" not in source
        assert "def reconcile" not in source


class TestNoBrokerLogic:
    def test_no_fyers_sdk_import(self):
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

    def test_no_broker_specific_terms_in_source(self):
        source = inspect.getsource(adapter_module).lower()
        for forbidden in ("place_order", "cancel_order", "get_open_positions", "profile"):
            assert forbidden not in source


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

class TestIsolation:
    def test_no_network_import(self):
        source = inspect.getsource(adapter_module)
        for forbidden in ("import requests", "import websocket", "import socket"):
            assert forbidden not in source

    def test_no_uuid4_used(self):
        tree = ast.parse(inspect.getsource(adapter_module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                raise AssertionError("uuid4 used")

    def test_no_idempotency_logic_of_its_own(self):
        source = inspect.getsource(adapter_module)
        assert "_cid_to_order_id" not in source
        assert "seen_client_order_ids" not in source
