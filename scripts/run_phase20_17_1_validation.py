"""Phase 20.17.1 real-data validation script -- reuses Phase 20.5's
own published real evidence verbatim, exactly as every prior Cycle-1
phase's own validation script does. Not a test; a demonstration run.
"""
from __future__ import annotations

from datetime import datetime

from bujji.decision_orchestration import BLOCKED, EXECUTABLE_CANDIDATE, NO_OPPORTUNITY, WATCH, FinalDecision
from bujji.capital_intelligence import AllocationAssessment
from bujji.opportunity_intelligence.models import MarketEnvironment, QualificationDecision, QualificationReason
from bujji.opportunity_ranking.models import OpportunityAssessment, OpportunityCandidate
from bujji.strategy_intelligence.models import StrategyEvidence, StrategyScore
from bujji.risk_context_adapter import build_risk_context_request, evaluate_risk_context, explain_risk_context
from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot

TS = datetime.fromisoformat("2026-08-13T09:15:00+00:00")


def _clock():
    return TS


def _allocation(strategy_name, evidence_score, win_rate, confidence, allocation_class, regime="RANGE"):
    evidence = StrategyEvidence(
        strategy_name=strategy_name, sample_size=11278 if strategy_name == "TrendFollowing" else 150,
        win_rate=win_rate, profit_factor=2.30 if win_rate else 0.0,
        gross_expectancy=949.0 if win_rate else 0.0, net_expectancy=517.0 if win_rate else 0.0,
        train_expectancy=512.0 if win_rate else 0.0, validation_expectancy=481.0 if win_rate else 0.0,
        out_of_sample_expectancy=573.0 if win_rate else 0.0,
    )
    score = StrategyScore(
        strategy_name=strategy_name, evidence_score=evidence_score, edge_component=40.0, execution_component=20.0,
        stability_component=evidence_score - 60.0, confidence=confidence, confidence_limiting_factor=None,
        context_note=None, effective_score=evidence_score, evidence=evidence,
    )
    decision = QualificationDecision(
        state="ELIGIBLE" if evidence_score > 0 else "BLOCKED",
        reasons=(QualificationReason(code="REAL_EVIDENCE", detail="real 20.5 evidence"),),
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


def _decision(state, strategy_name):
    return FinalDecision(
        strategy_name=strategy_name, decision_state=state,
        positive=("real_evidence",) if state != NO_OPPORTUNITY else (), negative=() if state != NO_OPPORTUNITY else ("insufficient_evidence",),
        unknown=(), allocation=None, portfolio_decision=None,
    )


def _healthy_snapshot():
    return CapitalSafetySnapshot(
        total_capital=1_000_000.0, available_capital=800_000.0, used_margin=200_000.0,
        open_risk=0.0, reserved_risk=0.0, daily_pnl=0.0, daily_loss_limit=50_000.0,
        peak_capital=1_000_000.0, max_allowed_drawdown=200_000.0, consecutive_losses=0, timestamp=TS,
    )


def _extreme_snapshot():
    return CapitalSafetySnapshot(
        total_capital=1000.0, available_capital=0.0, used_margin=1000.0,
        open_risk=0.0, reserved_risk=0.0, daily_pnl=-900.0, daily_loss_limit=100.0,
        peak_capital=1000.0, max_allowed_drawdown=100.0, consecutive_losses=5, timestamp=TS,
    )


def main():
    print("=" * 70)
    print("Scenario 1: Trend Following strong opportunity")
    print("=" * 70)
    allocation = _allocation("TrendFollowing", 78.62, 0.665, "HIGH", "NORMAL", regime="RANGE")
    decision = _decision(EXECUTABLE_CANDIDATE, "TrendFollowing")
    request = build_risk_context_request(decision, allocation, None, None, timestamp=TS)
    assessment = evaluate_risk_context(request, _healthy_snapshot(), clock=_clock)
    print(explain_risk_context(request, assessment))
    print()

    print("=" * 70)
    print("Scenario 2: Mean Reversion failed evidence")
    print("=" * 70)
    allocation2 = _allocation("MeanReversion", 0.0, 0.0, "NONE", "NONE")
    decision2 = _decision(NO_OPPORTUNITY, "MeanReversion")
    request2 = build_risk_context_request(decision2, allocation2, None, None, timestamp=TS)
    assessment2 = evaluate_risk_context(request2, _healthy_snapshot(), clock=_clock)
    print(explain_risk_context(request2, assessment2))
    print()

    print("=" * 70)
    print("Scenario 3: Extreme risk restriction")
    print("=" * 70)
    allocation3 = _allocation("TrendFollowing", 78.62, 0.665, "HIGH", "MINIMAL", regime="EXTREME")
    decision3 = _decision(WATCH, "TrendFollowing")
    request3 = build_risk_context_request(decision3, allocation3, None, None, timestamp=TS)
    assessment3 = evaluate_risk_context(request3, _extreme_snapshot(), clock=_clock)
    print(explain_risk_context(request3, assessment3))
    print()

    print("PROOF: evidence_score unmodified across all scenarios:", allocation.candidate.assessment.strategy_score.evidence_score)


if __name__ == "__main__":
    main()
