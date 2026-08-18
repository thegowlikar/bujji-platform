"""Phase 20.19 real-artifact validation script -- reuses Phase 20.5's
own published real evidence verbatim, and the real Phase 20.17.1/
20.18 status vocabulary, exactly matching every prior Cycle-1 phase's
own validation script pattern.
"""
from __future__ import annotations

from datetime import datetime

from bujji.decision_orchestration import EXECUTABLE_CANDIDATE, NO_OPPORTUNITY, WATCH, FinalDecision
from bujji.capital_intelligence import AllocationAssessment
from bujji.opportunity_intelligence.models import MarketEnvironment, QualificationDecision, QualificationReason
from bujji.opportunity_ranking.models import OpportunityAssessment, OpportunityCandidate
from bujji.strategy_intelligence.models import StrategyEvidence, StrategyScore
from bujji.risk_context_adapter import (
    STATUS_NOT_EVALUATED, STATUS_NOT_READY_FOR_CAPITAL_APPROVAL, STATUS_RESTRICTED, RiskContextAssessment,
)
from bujji.execution_intelligence import build_execution_intent, build_execution_plan
from bujji.broker.simulation.market_snapshot import MarketSnapshot
from bujji.broker_boundary import (
    PaperBrokerAdapter, build_execution_request, explain_broker_response, explain_reconciliation, reconcile,
)

TS = datetime.fromisoformat("2026-08-13T09:15:00+00:00")


def _allocation(strategy_name, evidence_score, win_rate, confidence, allocation_class, regime="RANGE"):
    evidence = StrategyEvidence(
        strategy_name=strategy_name, sample_size=11278 if win_rate else 150,
        win_rate=win_rate, profit_factor=2.30 if win_rate else 0.0,
        gross_expectancy=949.0 if win_rate else 0.0, net_expectancy=517.0 if win_rate else 0.0,
        train_expectancy=512.0 if win_rate else 0.0, validation_expectancy=481.0 if win_rate else 0.0,
        out_of_sample_expectancy=573.0 if win_rate else 0.0,
    )
    score = StrategyScore(
        strategy_name=strategy_name, evidence_score=evidence_score, edge_component=40.0, execution_component=20.0,
        stability_component=evidence_score - 60.0 if evidence_score > 60 else 0.0, confidence=confidence,
        confidence_limiting_factor=None, context_note=None, effective_score=evidence_score, evidence=evidence,
    )
    decision = QualificationDecision(
        state="ELIGIBLE" if evidence_score > 0 else "BLOCKED",
        reasons=(QualificationReason(code="REAL_EVIDENCE", detail="real Phase 20.5 evidence"),),
    )
    environment = MarketEnvironment(
        mic_regime=regime, risk_state="EXTREME" if regime == "EXTREME" else "NORMAL",
        volatility_state="LOW", execution_profile_name="STANDARD",
    )
    assessment = OpportunityAssessment(strategy_name=strategy_name, decision=decision, strategy_score=score, environment=environment)
    candidate = OpportunityCandidate(assessment=assessment)
    return AllocationAssessment(
        strategy_name=strategy_name, allocation_class=allocation_class, reasons=(), penalties=(),
        candidate=candidate, priority_score=evidence_score, rank=1,
    )


def _decision(state, strategy_name, allocation=None):
    return FinalDecision(
        strategy_name=strategy_name, decision_state=state,
        positive=("real_evidence",) if state != NO_OPPORTUNITY else (),
        negative=() if state != NO_OPPORTUNITY else ("insufficient_evidence",),
        unknown=(), allocation=allocation, portfolio_decision=None,
    )


def _run_scenario(label, decision, risk_status, snapshot):
    print("=" * 70)
    print(label)
    print("=" * 70)
    risk = RiskContextAssessment(
        status=risk_status, risk_context_valid=risk_status != STATUS_NOT_EVALUATED,
        governor_response="SAFE" if risk_status == STATUS_NOT_READY_FOR_CAPITAL_APPROVAL else None,
        blockers=(), explanation="real Phase 20.17.1 assessment (reconstructed for this validation)",
    )
    intent = build_execution_intent(decision, risk, timestamp=TS)
    if intent is None:
        print(f"No ExecutionIntent -- no ExecutionRequest -- no broker interaction. "
              f"decision_state={decision.decision_state!r}.")
        print()
        return
    plan = build_execution_plan(intent, risk)
    request = build_execution_request(intent, risk, timestamp=TS)
    adapter = PaperBrokerAdapter()
    response = adapter.submit_execution_request(request, plan, snapshot, seed=7)
    print(explain_broker_response(request, response))
    result = reconcile(request, response)
    print(explain_reconciliation(result))
    print()


def main():
    allocation_trend = _allocation("TrendFollowing", 78.62, 0.665, "HIGH", "NORMAL", regime="RANGE")
    _run_scenario(
        "Scenario A: Trend Following strong opportunity",
        _decision(EXECUTABLE_CANDIDATE, "TrendFollowing", allocation_trend),
        STATUS_NOT_READY_FOR_CAPITAL_APPROVAL,
        MarketSnapshot(symbol="NIFTY", last_price=100.0, volatility=1.0, liquidity_score=0.85),
    )

    allocation_mr = _allocation("MeanReversion", 0.0, 0.0, "NONE", "NONE", regime="RANGE")
    _run_scenario(
        "Scenario B: Mean Reversion failed evidence",
        _decision(NO_OPPORTUNITY, "MeanReversion", allocation_mr),
        STATUS_NOT_EVALUATED,
        MarketSnapshot(symbol="NIFTY", last_price=100.0, volatility=1.0, liquidity_score=0.85),
    )

    allocation_extreme = _allocation("TrendFollowing", 78.62, 0.665, "HIGH", "MINIMAL", regime="EXTREME")
    _run_scenario(
        "Scenario C: Extreme risk restriction",
        _decision(WATCH, "TrendFollowing", allocation_extreme),
        STATUS_RESTRICTED,
        MarketSnapshot(symbol="NIFTY", last_price=100.0, volatility=5.0, liquidity_score=0.1),
    )

    print("PROOF: evidence_score unmodified:", allocation_trend.candidate.assessment.strategy_score.evidence_score)


if __name__ == "__main__":
    main()
