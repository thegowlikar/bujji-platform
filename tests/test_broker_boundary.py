"""Tests -- Phase 20.19 Broker Abstraction & Paper Execution Boundary.
Zero network access, zero live-broker coupling anywhere in this file."""
from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pytest

from bujji.decision_orchestration import BLOCKED, EXECUTABLE_CANDIDATE, NO_OPPORTUNITY, WATCH, FinalDecision
from bujji.capital_intelligence import AllocationAssessment
from bujji.opportunity_intelligence.models import MarketEnvironment, QualificationDecision, QualificationReason
from bujji.opportunity_ranking.models import OpportunityAssessment, OpportunityCandidate
from bujji.strategy_intelligence.models import StrategyEvidence, StrategyScore
from bujji.risk_context_adapter import (
    RiskContextAssessment, STATUS_NOT_EVALUATED, STATUS_NOT_READY_FOR_CAPITAL_APPROVAL, STATUS_RESTRICTED,
)
from bujji.execution_intelligence import build_execution_intent, build_execution_plan
from bujji.broker.simulation.market_snapshot import MarketSnapshot
from bujji.broker_boundary import (
    CONSISTENT, INCONSISTENT, MISSING_RESPONSE,
    STATUS_ROUTED_TO_PAPER, STATUS_SIMULATION_UNAVAILABLE,
    PaperBrokerAdapter, build_execution_request, explain_broker_response, explain_reconciliation, reconcile,
)

TS = datetime.fromisoformat("2026-08-16T09:15:00+00:00")


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
        status=status, risk_context_valid=status not in (STATUS_NOT_EVALUATED,),
        governor_response=None, blockers=(), explanation="test fixture",
    )


def _snapshot():
    return MarketSnapshot(symbol="TEST", last_price=100.0, volatility=1.0, liquidity_score=0.9)


# --------------------------------------------------------------------- #
# 1. Contract correctness
# --------------------------------------------------------------------- #

def test_execution_request_translates_from_intent():
    allocation = _allocation()
    decision = _final_decision(EXECUTABLE_CANDIDATE, allocation=allocation)
    risk = _risk_assessment(STATUS_NOT_READY_FOR_CAPITAL_APPROVAL)
    intent = build_execution_intent(decision, risk, timestamp=TS)
    request = build_execution_request(intent, risk, timestamp=TS)
    assert request.execution_intent_id == "TrendFollowing"
    assert request.strategy_name == "TrendFollowing"
    assert request.risk_context_status == STATUS_NOT_READY_FOR_CAPITAL_APPROVAL
    assert request.direction is None


# --------------------------------------------------------------------- #
# 2. Paper routing
# --------------------------------------------------------------------- #

def test_execution_request_reaches_paper_adapter():
    allocation = _allocation()
    decision = _final_decision(EXECUTABLE_CANDIDATE, allocation=allocation)
    risk = _risk_assessment(STATUS_NOT_READY_FOR_CAPITAL_APPROVAL)
    intent = build_execution_intent(decision, risk, timestamp=TS)
    plan = build_execution_plan(intent, risk)
    request = build_execution_request(intent, risk, timestamp=TS)

    adapter = PaperBrokerAdapter()
    response = adapter.submit_execution_request(request, plan, _snapshot(), seed=1)
    assert response.adapter_name == "PAPER"
    assert response.status == STATUS_ROUTED_TO_PAPER
    assert response.fill_information is not None
    assert response.execution_reference == "PAPER-TrendFollowing"


# --------------------------------------------------------------------- #
# 3. No live broker leakage
# --------------------------------------------------------------------- #

_PKG_ROOT = Path(__file__).resolve().parent.parent / "bujji" / "broker_boundary"


def test_no_live_broker_imports_anywhere_in_package():
    forbidden_modules = (
        "bujji.broker.fyers", "bujji.broker.hybrid", "bujji.broker.base",
        "fyers_apiv3", "dhanhq", "zerodha", "kiteconnect",
    )
    for path in _PKG_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in forbidden_modules:
                    assert not node.module.startswith(forbidden), f"{node.module!r} imported in {path.name}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in forbidden_modules:
                        assert not alias.name.startswith(forbidden), f"{alias.name!r} imported in {path.name}"


# --------------------------------------------------------------------- #
# 4. No order capability
# --------------------------------------------------------------------- #

def test_no_order_placement_calls_anywhere_in_package():
    forbidden = (".place_order(", ".modify_order(", ".cancel_order(", ".get_open_positions(", ".get_order(")
    for path in _PKG_ROOT.glob("*.py"):
        source = path.read_text()
        for pattern in forbidden:
            assert pattern not in source, f"{pattern!r} found in {path.name}"


# --------------------------------------------------------------------- #
# 5. Reconciliation
# --------------------------------------------------------------------- #

def test_matching_states_reconcile_as_consistent():
    allocation = _allocation()
    decision = _final_decision(EXECUTABLE_CANDIDATE, allocation=allocation)
    risk = _risk_assessment(STATUS_NOT_READY_FOR_CAPITAL_APPROVAL)
    intent = build_execution_intent(decision, risk, timestamp=TS)
    plan = build_execution_plan(intent, risk)
    request = build_execution_request(intent, risk, timestamp=TS)

    adapter = PaperBrokerAdapter()
    response = adapter.submit_execution_request(request, plan, _snapshot(), seed=1)
    result = reconcile(request, response)
    assert result.consistency == CONSISTENT


def test_missing_response_reconciles_as_missing():
    allocation = _allocation()
    decision = _final_decision(EXECUTABLE_CANDIDATE, allocation=allocation)
    risk = _risk_assessment(STATUS_NOT_READY_FOR_CAPITAL_APPROVAL)
    intent = build_execution_intent(decision, risk, timestamp=TS)
    request = build_execution_request(intent, risk, timestamp=TS)
    result = reconcile(request, None)
    assert result.consistency == MISSING_RESPONSE


def test_inconsistent_state_explains_mismatch():
    from bujji.broker_boundary.models import BrokerResponse
    allocation = _allocation()
    decision = _final_decision(EXECUTABLE_CANDIDATE, allocation=allocation)
    risk = _risk_assessment(STATUS_NOT_READY_FOR_CAPITAL_APPROVAL)
    intent = build_execution_intent(decision, risk, timestamp=TS)
    request = build_execution_request(intent, risk, timestamp=TS)
    malformed_response = BrokerResponse(
        adapter_name="PAPER", status=STATUS_ROUTED_TO_PAPER, execution_reference="PAPER-X",
        fill_information=None, timestamp=TS,
    )
    result = reconcile(request, malformed_response)
    assert result.consistency == INCONSISTENT
    assert "mismatch" in result.explanation.lower()


# --------------------------------------------------------------------- #
# 6. Decision preservation
# --------------------------------------------------------------------- #

def test_broker_boundary_never_touches_evidence_or_ranking_fields():
    allocation = _allocation()
    original_score = allocation.candidate.assessment.strategy_score.evidence_score
    original_allocation_class = allocation.allocation_class
    decision = _final_decision(EXECUTABLE_CANDIDATE, allocation=allocation)
    risk = _risk_assessment(STATUS_NOT_READY_FOR_CAPITAL_APPROVAL)
    intent = build_execution_intent(decision, risk, timestamp=TS)
    plan = build_execution_plan(intent, risk)
    request = build_execution_request(intent, risk, timestamp=TS)
    adapter = PaperBrokerAdapter()
    response = adapter.submit_execution_request(request, plan, _snapshot(), seed=1)
    reconcile(request, response)

    assert allocation.candidate.assessment.strategy_score.evidence_score == original_score == 78.62
    assert allocation.allocation_class == original_allocation_class
    for obj in (request, response):
        for field in ("evidence_score", "priority_score", "rank", "allocation_class", "qualification_status"):
            assert not hasattr(obj, field)


# --------------------------------------------------------------------- #
# 7. Failure handling
# --------------------------------------------------------------------- #

def test_paper_adapter_unavailable_returns_honest_failure_never_live_fallback():
    allocation = _allocation()
    decision = _final_decision(WATCH, allocation=allocation)
    risk = _risk_assessment(STATUS_RESTRICTED)
    intent = build_execution_intent(decision, risk, timestamp=TS)
    plan = build_execution_plan(intent, risk)
    assert plan.simulation_required is False
    request = build_execution_request(intent, risk, timestamp=TS)
    adapter = PaperBrokerAdapter()
    response = adapter.submit_execution_request(request, plan, _snapshot(), seed=1)
    assert response.status == STATUS_SIMULATION_UNAVAILABLE
    assert response.adapter_name == "PAPER"       # never routed to any live adapter.
    assert response.fill_information is None


def test_get_execution_status_is_honestly_stateless():
    adapter = PaperBrokerAdapter()
    assert adapter.get_execution_status("PAPER-anything") is None


# --------------------------------------------------------------------- #
# 8. Explainability
# --------------------------------------------------------------------- #

def test_every_response_and_reconciliation_has_explanation():
    allocation = _allocation()
    decision = _final_decision(EXECUTABLE_CANDIDATE, allocation=allocation)
    risk = _risk_assessment(STATUS_NOT_READY_FOR_CAPITAL_APPROVAL)
    intent = build_execution_intent(decision, risk, timestamp=TS)
    plan = build_execution_plan(intent, risk)
    request = build_execution_request(intent, risk, timestamp=TS)
    adapter = PaperBrokerAdapter()
    response = adapter.submit_execution_request(request, plan, _snapshot(), seed=1)
    response_text = explain_broker_response(request, response)
    assert "TrendFollowing" in response_text

    result = reconcile(request, response)
    reconciliation_text = explain_reconciliation(result)
    assert "Reconciliation successful" in reconciliation_text
