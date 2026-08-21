"""Tests -- Phase 20.20 Shadow Result Composition & Pipeline Observability.
Zero network access, zero broker/execution coupling anywhere in this file."""
from __future__ import annotations

import ast
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from bujji.decision_orchestration import BLOCKED, EXECUTABLE_CANDIDATE, NO_OPPORTUNITY, WATCH, FinalDecision
from bujji.capital_intelligence import AllocationAssessment
from bujji.opportunity_intelligence.models import MarketEnvironment, QualificationDecision, QualificationReason
from bujji.opportunity_ranking.models import OpportunityAssessment, OpportunityCandidate
from bujji.strategy_intelligence.models import StrategyEvidence, StrategyScore
from bujji.risk_context_adapter import RiskContextAssessment, STATUS_NOT_EVALUATED, STATUS_NOT_READY_FOR_CAPITAL_APPROVAL
from bujji.execution_intelligence import build_execution_intent, build_execution_plan
from bujji.broker.simulation.market_snapshot import MarketSnapshot
from bujji.broker_boundary import CONSISTENT, INCONSISTENT, PaperBrokerAdapter, build_execution_request, reconcile
from bujji.state_persistence.store import EventStore
from bujji.shadow_result import (
    RUNTIME_DEGRADED, RUNTIME_EMPTY, RUNTIME_HEALTHY,
    STAGE_BROKER_ROUTED, STAGE_DECISION_ONLY, STAGE_EXECUTION_PLANNED, STAGE_RECONCILED, STAGE_RISK_EVALUATED,
    build_shadow_result_record, compute_pipeline_health, explain_pipeline_health, explain_shadow_result,
    read_all_shadow_results, record_shadow_result,
)
from bujji.shadow_result.models import ShadowResultRecord

TS = "2026-08-16T09:15:00+00:00"


def _allocation(strategy_name="TrendFollowing", allocation_class="NORMAL", confidence="HIGH", regime="RANGE"):
    evidence = StrategyEvidence(
        strategy_name=strategy_name, sample_size=11278, win_rate=0.665, profit_factor=2.30,
        gross_expectancy=949.0, net_expectancy=517.0, train_expectancy=512.0,
        validation_expectancy=481.0, out_of_sample_expectancy=573.0,
    )
    score = StrategyScore(
        strategy_name=strategy_name, evidence_score=78.62, edge_component=40.0, execution_component=20.0,
        stability_component=18.62, confidence=confidence, confidence_limiting_factor=None,
        context_note=None, effective_score=78.62, evidence=evidence,
    )
    decision = QualificationDecision(
        state="ELIGIBLE", reasons=(QualificationReason(code="REAL_EVIDENCE", detail="real evidence"),),
    )
    environment = MarketEnvironment(
        mic_regime=regime, risk_state="NORMAL", volatility_state="LOW", execution_profile_name="STANDARD",
    )
    assessment = OpportunityAssessment(strategy_name=strategy_name, decision=decision, strategy_score=score, environment=environment)
    candidate = OpportunityCandidate(assessment=assessment)
    return AllocationAssessment(
        strategy_name=strategy_name, allocation_class=allocation_class, reasons=(), penalties=(),
        candidate=candidate, priority_score=78.62, rank=1,
    )


def _final_decision(decision_state, strategy_name="TrendFollowing", allocation=None):
    return FinalDecision(
        strategy_name=strategy_name, decision_state=decision_state,
        positive=("real_evidence",), negative=(), unknown=(), allocation=allocation, portfolio_decision=None,
    )


def _risk_assessment(status):
    return RiskContextAssessment(
        status=status, risk_context_valid=status != STATUS_NOT_EVALUATED,
        governor_response=None, blockers=(), explanation="test fixture",
    )


def _snapshot():
    return MarketSnapshot(symbol="TEST", last_price=100.0, volatility=1.0, liquidity_score=0.9)


def _full_pipeline(decision_state=EXECUTABLE_CANDIDATE, risk_status=STATUS_NOT_READY_FOR_CAPITAL_APPROVAL):
    allocation = _allocation()
    decision = _final_decision(decision_state, allocation=allocation)
    risk = _risk_assessment(risk_status)
    intent = build_execution_intent(decision, risk, timestamp=datetime.fromisoformat(TS))
    plan = build_execution_plan(intent, risk) if intent is not None else None
    request = build_execution_request(intent, risk, timestamp=datetime.fromisoformat(TS)) if intent is not None else None
    adapter = PaperBrokerAdapter()
    response = adapter.submit_execution_request(request, plan, _snapshot(), seed=1) if request is not None else None
    reconciliation = reconcile(request, response) if request is not None else None
    return decision, risk, intent, plan, response, reconciliation


# --------------------------------------------------------------------- #
# 1. Normal lifecycle
# --------------------------------------------------------------------- #

def test_normal_lifecycle_reaches_reconciled_stage():
    decision, risk, intent, plan, response, reconciliation = _full_pipeline()
    record = build_shadow_result_record(
        decision, risk, intent, plan, response, reconciliation, session_id="s1", timestamp=TS,
    )
    assert record.pipeline_stage_reached == STAGE_RECONCILED
    assert record.execution_intent_created is True
    assert record.broker_response_status is not None
    assert record.reconciliation_consistency == CONSISTENT


# --------------------------------------------------------------------- #
# 2. Missing intelligence (risk context never evaluated)
# --------------------------------------------------------------------- #

def test_missing_risk_context_stops_at_decision_only():
    allocation = _allocation()
    decision = _final_decision(EXECUTABLE_CANDIDATE, allocation=allocation)
    record = build_shadow_result_record(decision, None, None, None, None, None, session_id="s1", timestamp=TS)
    assert record.pipeline_stage_reached == STAGE_DECISION_ONLY
    assert record.risk_context_status is None
    assert record.execution_intent_created is False


# --------------------------------------------------------------------- #
# 3. Rejected decision
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("state", [NO_OPPORTUNITY, BLOCKED])
def test_rejected_decision_never_progresses_past_decision_only(state):
    decision = _final_decision(state)
    risk = _risk_assessment(STATUS_NOT_EVALUATED)
    record = build_shadow_result_record(decision, risk, None, None, None, None, session_id="s1", timestamp=TS)
    assert record.pipeline_stage_reached == STAGE_DECISION_ONLY
    assert record.decision_state == state


# --------------------------------------------------------------------- #
# 4. Execution failure
# --------------------------------------------------------------------- #

def test_execution_unavailable_still_records_honest_stage():
    allocation = _allocation()
    decision = _final_decision(WATCH, allocation=allocation)
    from bujji.risk_context_adapter import STATUS_RESTRICTED
    risk = _risk_assessment(STATUS_RESTRICTED)
    intent = build_execution_intent(decision, risk, timestamp=datetime.fromisoformat(TS))
    plan = build_execution_plan(intent, risk)
    assert plan.simulation_required is False
    request = build_execution_request(intent, risk, timestamp=datetime.fromisoformat(TS))
    adapter = PaperBrokerAdapter()
    response = adapter.submit_execution_request(request, plan, _snapshot(), seed=1)
    reconciliation = reconcile(request, response)
    record = build_shadow_result_record(
        decision, risk, intent, plan, response, reconciliation, session_id="s1", timestamp=TS,
    )
    assert record.pipeline_stage_reached == STAGE_RECONCILED
    assert record.execution_result_status is None
    assert record.reconciliation_consistency == CONSISTENT   # "correctly skipped" is itself consistent.


# --------------------------------------------------------------------- #
# 5. Reconciliation mismatch
# --------------------------------------------------------------------- #

def test_reconciliation_mismatch_is_honestly_recorded():
    from bujji.broker_boundary.models import BrokerResponse
    from bujji.broker_boundary import STATUS_ROUTED_TO_PAPER
    decision, risk, intent, plan, _, _ = _full_pipeline()
    request = build_execution_request(intent, risk, timestamp=datetime.fromisoformat(TS))
    malformed_response = BrokerResponse(
        adapter_name="PAPER", status=STATUS_ROUTED_TO_PAPER, execution_reference="PAPER-X",
        fill_information=None, timestamp=datetime.fromisoformat(TS),
    )
    result = reconcile(request, malformed_response)
    record = build_shadow_result_record(
        decision, risk, intent, plan, malformed_response, result, session_id="s1", timestamp=TS,
    )
    assert record.reconciliation_consistency == INCONSISTENT


# --------------------------------------------------------------------- #
# 6. Restart recovery
# --------------------------------------------------------------------- #

def test_restart_recovery_via_fresh_event_store_instance():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "shadow_results.jsonl")
        decision, risk, intent, plan, response, reconciliation = _full_pipeline()
        record = build_shadow_result_record(
            decision, risk, intent, plan, response, reconciliation, session_id="s1", timestamp=TS,
        )
        store1 = EventStore(path)
        record_shadow_result(store1, record)
        del store1   # simulate process exit -- no shared in-memory state survives.

        store2 = EventStore(path)   # a fresh instance, as a restarted process would construct.
        recovered = read_all_shadow_results(store2)
        assert len(recovered) == 1
        assert recovered[0] == record


# --------------------------------------------------------------------- #
# 7. Persistence integrity
# --------------------------------------------------------------------- #

def test_duplicate_write_is_deduplicated_on_read():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "shadow_results.jsonl")
        decision, risk, intent, plan, response, reconciliation = _full_pipeline()
        record = build_shadow_result_record(
            decision, risk, intent, plan, response, reconciliation, session_id="s1", timestamp=TS,
        )
        store = EventStore(path)
        record_shadow_result(store, record)
        record_shadow_result(store, record)   # crash-and-retry simulation -- same record_id.
        recovered = read_all_shadow_results(store)
        assert len(recovered) == 1


def test_health_report_reflects_persisted_records_exactly():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "shadow_results.jsonl")
        store = EventStore(path)
        decision, risk, intent, plan, response, reconciliation = _full_pipeline()
        record = build_shadow_result_record(
            decision, risk, intent, plan, response, reconciliation, session_id="s1", timestamp=TS,
        )
        record_shadow_result(store, record)
        recovered = read_all_shadow_results(store)
        report = compute_pipeline_health(recovered)
        assert report.cycles_total == 1
        assert report.runtime_status == RUNTIME_HEALTHY


def test_empty_records_produce_honest_empty_health():
    report = compute_pipeline_health([])
    assert report.runtime_status == RUNTIME_EMPTY
    assert report.cycles_total == 0


# --------------------------------------------------------------------- #
# 8. Explainability
# --------------------------------------------------------------------- #

def test_every_record_and_health_report_has_explanation():
    decision, risk, intent, plan, response, reconciliation = _full_pipeline()
    record = build_shadow_result_record(
        decision, risk, intent, plan, response, reconciliation, session_id="s1", timestamp=TS,
    )
    text = explain_shadow_result(record)
    assert "TrendFollowing" in text
    assert STAGE_RECONCILED in text

    report = compute_pipeline_health([record])
    health_text = explain_pipeline_health(report)
    assert RUNTIME_HEALTHY in health_text


# --------------------------------------------------------------------- #
# 9. Safety boundary
# --------------------------------------------------------------------- #

_PKG_ROOT = Path(__file__).resolve().parent.parent / "bujji" / "shadow_result"


def test_no_forbidden_broker_calls_anywhere_in_package():
    forbidden = (".place_order(", ".modify_order(", ".cancel_order(", ".get_open_positions(", ".get_order(")
    for path in _PKG_ROOT.glob("*.py"):
        source = path.read_text()
        for pattern in forbidden:
            assert pattern not in source, f"{pattern!r} found in {path.name}"


def test_no_live_broker_imports_anywhere_in_package():
    forbidden_modules = ("bujji.broker.fyers", "bujji.broker.hybrid", "bujji.broker.base", "fyers_apiv3", "dhanhq")
    for path in _PKG_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in forbidden_modules:
                    assert not node.module.startswith(forbidden), f"{node.module!r} imported in {path.name}"


def test_package_never_recomputes_evidence_or_qualification():
    """Matches actual attribute-access syntax (`.evidence_score`,
    `record_id="evidence_score"`-style dataclass fields), not the bare
    substring -- `__init__.py`'s own disclosure docstring legitimately
    NAMES these terms while explaining they are never computed here."""
    forbidden_field_access = (".evidence_score", ".effective_score", ".qualification_status", ".priority_score")
    for path in _PKG_ROOT.glob("*.py"):
        source = path.read_text()
        for term in forbidden_field_access:
            assert term not in source, f"{term!r} found in {path.name}"
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assert node.target.id not in (
                    "evidence_score", "effective_score", "qualification_status", "priority_score",
                ), f"dataclass field {node.target.id!r} found in {path.name}"


def test_shadow_result_record_never_bare_named_shadow_result():
    """Disclosed collision guard: this package must export
    `ShadowResultRecord`, never a bare `ShadowResult` symbol that could
    be confused with `bujji.production_runtime.runtime.ShadowResult`."""
    import bujji.shadow_result as pkg
    assert not hasattr(pkg, "ShadowResult")
    assert hasattr(pkg, "ShadowResultRecord")
