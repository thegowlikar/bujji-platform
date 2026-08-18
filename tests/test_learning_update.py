"""Tests -- Phase 20.21 Learning Update Layer.
Zero network access, zero broker/execution coupling anywhere in this file."""
from __future__ import annotations

import ast
import tempfile
from pathlib import Path

import pytest

from bujji.shadow_result import (
    ShadowResultRecord, STAGE_DECISION_ONLY, STAGE_RECONCILED,
)
from bujji.decision_orchestration import BLOCKED, EXECUTABLE_CANDIDATE, NO_OPPORTUNITY, WATCH
from bujji.risk_context_adapter import (
    STATUS_NOT_READY_FOR_CAPITAL_APPROVAL, STATUS_RESTRICTED, STATUS_NOT_EVALUATED,
)
from bujji.execution_intelligence import STATUS_FILLED, STATUS_REJECTED
from bujji.broker_boundary import CONSISTENT, INCONSISTENT
from bujji.state_persistence.store import EventStore
from bujji.market_memory.store import record_market_memory
from bujji.market_memory.models import MarketMemoryRecord
from bujji.learning_update import (
    CONFIRMED_PATTERN, CONFLICTING_SIGNAL, FAILED_PATTERN, INSUFFICIENT_RESULT, UNAVAILABLE_DATA,
    evaluate_shadow_result_for_learning, explain_learning_update, read_all_learning_updates,
    record_learning_update,
)

TS = "2026-08-16T09:15:00+00:00"


def _shadow_result(
    strategy_name="TrendFollowing", decision_state=EXECUTABLE_CANDIDATE,
    risk_context_status=STATUS_NOT_READY_FOR_CAPITAL_APPROVAL, pipeline_stage=STAGE_RECONCILED,
    execution_intent_created=True, execution_simulation_required=True,
    broker_response_status="ROUTED_TO_PAPER", broker_adapter_name="PAPER",
    execution_result_status=STATUS_FILLED, reconciliation_consistency=CONSISTENT,
):
    return ShadowResultRecord(
        record_id=f"SHDRES-{strategy_name}-{TS}", session_id="s1", strategy_name=strategy_name, timestamp=TS,
        decision_state=decision_state, pipeline_stage_reached=pipeline_stage,
        risk_context_status=risk_context_status, execution_intent_created=execution_intent_created,
        execution_simulation_required=execution_simulation_required,
        broker_response_status=broker_response_status, broker_adapter_name=broker_adapter_name,
        execution_result_status=execution_result_status, reconciliation_consistency=reconciliation_consistency,
    )


# --------------------------------------------------------------------- #
# 1. Successful shadow result creates learning update
# --------------------------------------------------------------------- #

def test_successful_shadow_result_creates_confirmed_pattern():
    shadow_result = _shadow_result()
    update = evaluate_shadow_result_for_learning(shadow_result, created_at=TS)
    assert update.learning_classification == CONFIRMED_PATTERN
    assert update.source_shadow_result_id == shadow_result.record_id
    assert update.strategy_family == "TrendFollowing"


# --------------------------------------------------------------------- #
# 2. Failed result creates failed-pattern memory
# --------------------------------------------------------------------- #

def test_rejected_fill_creates_failed_pattern():
    shadow_result = _shadow_result(execution_result_status=STATUS_REJECTED)
    update = evaluate_shadow_result_for_learning(shadow_result, created_at=TS)
    assert update.learning_classification == FAILED_PATTERN


# --------------------------------------------------------------------- #
# 3. BLOCKED decision creates no false success
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("state", [NO_OPPORTUNITY, BLOCKED])
def test_rejected_decision_never_creates_false_success(state):
    shadow_result = _shadow_result(
        decision_state=state, risk_context_status=None, pipeline_stage=STAGE_DECISION_ONLY,
        execution_intent_created=False, execution_simulation_required=None,
        broker_response_status=None, broker_adapter_name=None,
        execution_result_status=None, reconciliation_consistency=None,
    )
    update = evaluate_shadow_result_for_learning(shadow_result, created_at=TS)
    assert update.learning_classification == INSUFFICIENT_RESULT
    assert update.learning_classification != CONFIRMED_PATTERN


# --------------------------------------------------------------------- #
# 4. Missing outcome handled honestly
# --------------------------------------------------------------------- #

def test_reconciled_but_no_fill_outcome_is_insufficient():
    shadow_result = _shadow_result(
        decision_state=WATCH, risk_context_status=STATUS_RESTRICTED,
        execution_simulation_required=False, broker_response_status="SIMULATION_UNAVAILABLE",
        execution_result_status=None, reconciliation_consistency=CONSISTENT,
    )
    update = evaluate_shadow_result_for_learning(shadow_result, created_at=TS)
    assert update.learning_classification == INSUFFICIENT_RESULT


def test_risk_cleared_but_never_reconciled_is_unavailable_data():
    shadow_result = _shadow_result(reconciliation_consistency=None, broker_response_status=None,
                                     broker_adapter_name=None, execution_result_status=None,
                                     pipeline_stage=STAGE_RECONCILED)
    update = evaluate_shadow_result_for_learning(shadow_result, created_at=TS)
    assert update.learning_classification == UNAVAILABLE_DATA


def test_reconciliation_mismatch_is_conflicting_signal():
    shadow_result = _shadow_result(reconciliation_consistency=INCONSISTENT)
    update = evaluate_shadow_result_for_learning(shadow_result, created_at=TS)
    assert update.learning_classification == CONFLICTING_SIGNAL


# --------------------------------------------------------------------- #
# 5-7. Evidence / qualification / ranking unchanged before/after
# --------------------------------------------------------------------- #

def test_learning_update_never_touches_evidence_qualification_ranking():
    shadow_result = _shadow_result()
    update = evaluate_shadow_result_for_learning(shadow_result, created_at=TS)
    for obj in (shadow_result, update):
        for field in ("evidence_score", "qualification_score", "ranking_score", "allocation_score", "risk_score"):
            assert not hasattr(obj, field)


# --------------------------------------------------------------------- #
# 8. Risk governor boundary preserved
# --------------------------------------------------------------------- #

def test_restricted_risk_never_produces_confirmed_pattern():
    shadow_result = _shadow_result(
        decision_state=WATCH, risk_context_status=STATUS_RESTRICTED,
        execution_simulation_required=False, broker_response_status="SIMULATION_UNAVAILABLE",
        execution_result_status=None,
    )
    update = evaluate_shadow_result_for_learning(shadow_result, created_at=TS)
    assert update.learning_classification != CONFIRMED_PATTERN


# --------------------------------------------------------------------- #
# 9. Execution boundary preserved
# --------------------------------------------------------------------- #

def test_evaluator_never_calls_simulate_execution_or_places_orders():
    pkg_root = Path(__file__).resolve().parent.parent / "bujji" / "learning_update"
    for path in pkg_root.glob("*.py"):
        source = path.read_text()
        assert "simulate_execution(" not in source
        assert ".place_order(" not in source


# --------------------------------------------------------------------- #
# 10. Restart recovery
# --------------------------------------------------------------------- #

def test_restart_recovery_via_fresh_event_store_instance():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "memory.jsonl")
        shadow_result = _shadow_result()
        update = evaluate_shadow_result_for_learning(shadow_result, created_at=TS)
        store1 = EventStore(path)
        record_learning_update(store1, update, session_id="s1")
        del store1

        store2 = EventStore(path)
        recovered = read_all_learning_updates(store2)
        assert len(recovered) == 1
        assert recovered[0] == update


# --------------------------------------------------------------------- #
# 11. Duplicate learning update idempotency
# --------------------------------------------------------------------- #

def test_duplicate_write_is_deduplicated_on_read():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "memory.jsonl")
        shadow_result = _shadow_result()
        update = evaluate_shadow_result_for_learning(shadow_result, created_at=TS)
        store = EventStore(path)
        record_learning_update(store, update, session_id="s1")
        record_learning_update(store, update, session_id="s1")   # crash-and-retry simulation.
        recovered = read_all_learning_updates(store)
        assert len(recovered) == 1


# --------------------------------------------------------------------- #
# 12. Explainability
# --------------------------------------------------------------------- #

def test_every_learning_update_has_explanation():
    shadow_result = _shadow_result()
    update = evaluate_shadow_result_for_learning(shadow_result, created_at=TS)
    text = explain_learning_update(update)
    assert "TrendFollowing" in text
    assert CONFIRMED_PATTERN in text


# --------------------------------------------------------------------- #
# 13. No broker imports
# --------------------------------------------------------------------- #

_PKG_ROOT = Path(__file__).resolve().parent.parent / "bujji" / "learning_update"


def test_no_live_broker_imports_anywhere_in_package():
    forbidden_modules = ("bujji.broker.fyers", "bujji.broker.hybrid", "bujji.broker.base", "fyers_apiv3", "dhanhq")
    for path in _PKG_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in forbidden_modules:
                    assert not node.module.startswith(forbidden), f"{node.module!r} imported in {path.name}"


# --------------------------------------------------------------------- #
# 14. No order vocabulary
# --------------------------------------------------------------------- #

def test_no_order_placement_calls_anywhere_in_package():
    forbidden = (".place_order(", ".modify_order(", ".cancel_order(", ".get_open_positions(", ".get_order(")
    for path in _PKG_ROOT.glob("*.py"):
        source = path.read_text()
        for pattern in forbidden:
            assert pattern not in source, f"{pattern!r} found in {path.name}"


# --------------------------------------------------------------------- #
# 15. Memory write verification (coexists with bujji.market_memory in the same file)
# --------------------------------------------------------------------- #

def test_learning_update_coexists_with_market_memory_in_same_store():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "shared_memory.jsonl")
        store = EventStore(path)

        market_record = MarketMemoryRecord(
            memory_id="MKTMEM-test", as_of_time=TS, market_regime="RANGE", volatility_state="LOW",
            risk_state="NORMAL", data_quality="COMPLETE", evidence=(),
        )
        record_market_memory(store, market_record, session_id="s1")

        shadow_result = _shadow_result()
        update = evaluate_shadow_result_for_learning(shadow_result, created_at=TS)
        record_learning_update(store, update, session_id="s1")

        from bujji.market_memory.store import read_all_market_memories
        recovered_market = read_all_market_memories(store)
        recovered_learning = read_all_learning_updates(store)
        assert len(recovered_market) == 1
        assert len(recovered_learning) == 1
        assert recovered_market[0] == market_record
        assert recovered_learning[0] == update
