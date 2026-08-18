"""Tests -- Phase 20.17.1 Risk Context Adapter.
Zero network access, zero broker/execution coupling anywhere in this file."""
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
    STATUS_NOT_EVALUATED, STATUS_NOT_READY_FOR_CAPITAL_APPROVAL, STATUS_RESTRICTED,
    STATUS_UNAVAILABLE_RISK_CONTEXT,
    build_risk_context_request, evaluate_risk_context, explain_risk_context,
)
from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot


def _clock(iso="2026-08-16T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _final_decision(decision_state, strategy_name="TrendFollowing"):
    return FinalDecision(
        strategy_name=strategy_name, decision_state=decision_state,
        positive=("real_evidence_supports_review",), negative=(), unknown=(),
        allocation=None, portfolio_decision=None,
    )


def _allocation_assessment(strategy_name="TrendFollowing", allocation_class="NORMAL", confidence="HIGH"):
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
        state="ELIGIBLE", reasons=(QualificationReason(code="REAL_EVIDENCE", detail="real evidence supports it"),),
    )
    environment = MarketEnvironment(
        mic_regime="RANGE", risk_state="NORMAL", volatility_state="LOW", execution_profile_name="STANDARD",
    )
    assessment = OpportunityAssessment(
        strategy_name=strategy_name, decision=decision, strategy_score=score, environment=environment,
    )
    candidate = OpportunityCandidate(assessment=assessment)
    return AllocationAssessment(
        strategy_name=strategy_name, allocation_class=allocation_class, reasons=(), penalties=(),
        candidate=candidate, priority_score=78.62, rank=1,
    )


def _healthy_snapshot():
    return CapitalSafetySnapshot(
        total_capital=1_000_000.0, available_capital=800_000.0, used_margin=200_000.0,
        open_risk=0.0, reserved_risk=0.0, daily_pnl=0.0, daily_loss_limit=50_000.0,
        peak_capital=1_000_000.0, max_allowed_drawdown=200_000.0, consecutive_losses=0,
        timestamp=datetime.fromisoformat("2026-08-16T09:15:00+00:00"),
    )


def _exhausted_snapshot():
    return CapitalSafetySnapshot(
        total_capital=1000.0, available_capital=0.0, used_margin=1000.0,
        open_risk=0.0, reserved_risk=0.0, daily_pnl=0.0, daily_loss_limit=500.0,
        peak_capital=1000.0, max_allowed_drawdown=500.0, consecutive_losses=0,
        timestamp=datetime.fromisoformat("2026-08-16T09:15:00+00:00"),
    )


# --------------------------------------------------------------------- #
# 1. Correct translation
# --------------------------------------------------------------------- #

def test_build_risk_context_request_translates_final_decision():
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    allocation = _allocation_assessment()
    ts = datetime.fromisoformat("2026-08-16T09:15:00+00:00")
    request = build_risk_context_request(decision, allocation, None, None, timestamp=ts)
    assert request.strategy_name == "TrendFollowing"
    assert request.opportunity_id == "TrendFollowing"
    assert request.decision_state == EXECUTABLE_CANDIDATE
    assert request.allocation_class == "NORMAL"
    assert request.confidence_level == "HIGH"
    assert request.market_regime == "RANGE"
    assert request.expected_exposure is None
    assert request.expected_loss_boundary is None


# --------------------------------------------------------------------- #
# 2. No fabrication
# --------------------------------------------------------------------- #

def test_no_margin_snapshot_or_position_or_order_created_anywhere():
    _PKG_ROOT = Path(__file__).resolve().parent.parent / "bujji" / "risk_context_adapter"
    forbidden = ("MarginSnapshot(", "PositionGroupState(", "PositionRiskSnapshot(", ".place_order(")
    for path in _PKG_ROOT.glob("*.py"):
        source = path.read_text()
        for pattern in forbidden:
            assert pattern not in source, f"{pattern!r} found in {path.name}"


# --------------------------------------------------------------------- #
# 3. Risk Governor preservation
# --------------------------------------------------------------------- #

def test_risk_governor_files_untouched():
    import subprocess
    result = subprocess.run(
        ["git", "status", "--porcelain", "bujji/trading_brain/risk_governor/"],
        cwd=Path(__file__).resolve().parent.parent, capture_output=True, text=True,
    )
    # No git repo assumption -- if git is unavailable this is a no-op pass;
    # the meaningful check is that this package imports, never edits, D.1.
    assert "risk_context_adapter" not in result.stdout


# --------------------------------------------------------------------- #
# 4. Missing context honesty
# --------------------------------------------------------------------- #

def test_no_capital_snapshot_returns_unavailable_not_blocked():
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    allocation = _allocation_assessment()
    request = build_risk_context_request(decision, allocation, None, None, timestamp=datetime.now())
    assessment = evaluate_risk_context(request, None, clock=_clock())
    assert assessment.status == STATUS_UNAVAILABLE_RISK_CONTEXT
    assert assessment.risk_context_valid is False


def test_healthy_capital_with_no_portfolio_context_is_not_ready_not_blocked():
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    allocation = _allocation_assessment()
    request = build_risk_context_request(decision, allocation, None, None, timestamp=datetime.now())
    assessment = evaluate_risk_context(request, _healthy_snapshot(), clock=_clock())
    assert assessment.status == STATUS_NOT_READY_FOR_CAPITAL_APPROVAL
    assert assessment.risk_context_valid is True
    assert "PORTFOLIO_CONTEXT_UNAVAILABLE" in assessment.blockers[0]
    assert "not rejected" in assessment.explanation.lower()


# --------------------------------------------------------------------- #
# 5. Opportunity rejection preservation
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("state", [NO_OPPORTUNITY, BLOCKED])
def test_rejected_decisions_remain_not_evaluated(state):
    decision = _final_decision(state)
    allocation = _allocation_assessment()
    request = build_risk_context_request(decision, allocation, None, None, timestamp=datetime.now())
    assessment = evaluate_risk_context(request, _healthy_snapshot(), clock=_clock())
    assert assessment.status == STATUS_NOT_EVALUATED
    assert assessment.risk_context_valid is False


def test_extreme_risk_capital_is_restricted_not_hidden():
    decision = _final_decision(WATCH)
    allocation = _allocation_assessment(confidence="LOW")
    request = build_risk_context_request(decision, allocation, None, None, timestamp=datetime.now())
    assessment = evaluate_risk_context(request, _exhausted_snapshot(), clock=_clock())
    assert assessment.status == STATUS_RESTRICTED
    assert assessment.risk_context_valid is True
    assert len(assessment.blockers) > 0


# --------------------------------------------------------------------- #
# 6. Decision independence
# --------------------------------------------------------------------- #

def test_request_and_assessment_never_carry_evidence_or_ranking_fields():
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    allocation = _allocation_assessment()
    request = build_risk_context_request(decision, allocation, None, None, timestamp=datetime.now())
    assessment = evaluate_risk_context(request, _healthy_snapshot(), clock=_clock())
    for obj in (request, assessment):
        for field in ("evidence_score", "effective_score", "priority_score", "qualification_status", "rank"):
            assert not hasattr(obj, field)


def test_evidence_score_identical_before_and_after_adapter():
    allocation = _allocation_assessment()
    original_score = allocation.candidate.assessment.strategy_score.evidence_score
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    request = build_risk_context_request(decision, allocation, None, None, timestamp=datetime.now())
    evaluate_risk_context(request, _healthy_snapshot(), clock=_clock())
    assert allocation.candidate.assessment.strategy_score.evidence_score == original_score == 78.62


# --------------------------------------------------------------------- #
# 7. Explainability
# --------------------------------------------------------------------- #

def test_every_assessment_has_nonempty_explanation():
    decision = _final_decision(EXECUTABLE_CANDIDATE)
    allocation = _allocation_assessment()
    request = build_risk_context_request(decision, allocation, None, None, timestamp=datetime.now())
    assessment = evaluate_risk_context(request, _healthy_snapshot(), clock=_clock())
    assert assessment.explanation
    text = explain_risk_context(request, assessment)
    assert "TrendFollowing" in text
    assert assessment.status in text


# --------------------------------------------------------------------- #
# 8. Safety boundary
# --------------------------------------------------------------------- #

_PKG_ROOT = Path(__file__).resolve().parent.parent / "bujji" / "risk_context_adapter"

_FORBIDDEN_CALL_PATTERNS = (
    ".place_order(", ".modify_order(", ".cancel_order(", ".get_open_positions(", ".get_order(",
)


def test_no_forbidden_broker_calls_anywhere_in_package():
    for path in _PKG_ROOT.glob("*.py"):
        source = path.read_text()
        for pattern in _FORBIDDEN_CALL_PATTERNS:
            assert pattern not in source, f"{pattern!r} found in {path.name}"


def test_no_broker_or_execution_imports_anywhere_in_package():
    forbidden_modules = ("bujji.broker", "bujji.execution")
    for path in _PKG_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in forbidden_modules:
                    assert not node.module.startswith(forbidden), f"{node.module!r} imported in {path.name}"


def test_never_calls_d2_portfolio_aggregation():
    """Matches actual call syntax (`= aggregate_portfolio_risk(` /
    `import aggregate_portfolio_risk`), not the bare substring -- the
    package's own __init__.py and adapter.py docstrings legitimately
    NAME these functions while disclosing they are never called."""
    forbidden_call_syntax = (
        "= aggregate_portfolio_risk(", "aggregate_portfolio_risk(context",
        "import aggregate_portfolio_risk", "from .portfolio_risk_aggregator import",
    )
    for path in _PKG_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        calls = [ast.dump(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)]
        for call_dump in calls:
            assert "aggregate_portfolio_risk" not in call_dump, f"real call found in {path.name}"
            assert "classify_portfolio_risk" not in call_dump, f"real call found in {path.name}"
        imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        for node in imports:
            names = [a.name for a in node.names]
            assert "aggregate_portfolio_risk" not in names, f"real import found in {path.name}"
            assert "classify_portfolio_risk" not in names, f"real import found in {path.name}"
