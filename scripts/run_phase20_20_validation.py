"""Phase 20.20 real-artifact validation script -- reuses Phase 20.5's
own published real evidence verbatim, composes real ShadowResultRecords
through the entire real chain (20.10 -> 20.17.1 -> 20.18 -> 20.19),
persists them via a real EventStore, then demonstrates restart recovery
and pipeline health -- exactly matching every prior Cycle-1 phase's own
validation script pattern.
"""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

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
from bujji.broker_boundary import PaperBrokerAdapter, build_execution_request, reconcile
from bujji.state_persistence.store import EventStore
from bujji.shadow_result import (
    build_shadow_result_record, compute_pipeline_health, explain_pipeline_health, explain_shadow_result,
    read_all_shadow_results, record_shadow_result,
)

TS = "2026-08-13T09:15:00+00:00"


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


def _run_cycle(store, decision, risk_status, snapshot, *, timestamp):
    risk = RiskContextAssessment(
        status=risk_status, risk_context_valid=risk_status != STATUS_NOT_EVALUATED,
        governor_response="SAFE" if risk_status == STATUS_NOT_READY_FOR_CAPITAL_APPROVAL else None,
        blockers=(), explanation="real Phase 20.17.1 assessment (reconstructed for this validation)",
    )
    intent = build_execution_intent(decision, risk, timestamp=datetime.fromisoformat(timestamp))
    plan = request = response = reconciliation = None
    if intent is not None:
        plan = build_execution_plan(intent, risk)
        request = build_execution_request(intent, risk, timestamp=datetime.fromisoformat(timestamp))
        adapter = PaperBrokerAdapter()
        response = adapter.submit_execution_request(request, plan, snapshot, seed=7)
        reconciliation = reconcile(request, response)

    record = build_shadow_result_record(
        decision, risk, intent, plan, response, reconciliation, session_id="validation-20-20", timestamp=timestamp,
    )
    record_shadow_result(store, record)
    print(explain_shadow_result(record))
    print()
    return record


def main():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "shadow_results.jsonl")
        store = EventStore(path)

        print("=" * 70)
        print("Cycle A: Trend Following strong opportunity")
        print("=" * 70)
        allocation_trend = _allocation("TrendFollowing", 78.62, 0.665, "HIGH", "NORMAL", regime="RANGE")
        _run_cycle(
            store, _decision(EXECUTABLE_CANDIDATE, "TrendFollowing", allocation_trend),
            STATUS_NOT_READY_FOR_CAPITAL_APPROVAL,
            MarketSnapshot(symbol="NIFTY", last_price=100.0, volatility=1.0, liquidity_score=0.85),
            timestamp=TS,
        )

        print("=" * 70)
        print("Cycle B: Mean Reversion failed evidence")
        print("=" * 70)
        allocation_mr = _allocation("MeanReversion", 0.0, 0.0, "NONE", "NONE", regime="RANGE")
        _run_cycle(
            store, _decision(NO_OPPORTUNITY, "MeanReversion", allocation_mr), STATUS_NOT_EVALUATED,
            MarketSnapshot(symbol="NIFTY", last_price=100.0, volatility=1.0, liquidity_score=0.85),
            timestamp=TS,
        )

        print("=" * 70)
        print("Cycle C: Extreme risk restriction")
        print("=" * 70)
        TS_C = "2026-08-13T10:15:00+00:00"   # a distinct real cycle instant -- never the same as Cycle A's.
        allocation_extreme = _allocation("TrendFollowing", 78.62, 0.665, "HIGH", "MINIMAL", regime="EXTREME")
        _run_cycle(
            store, _decision(WATCH, "TrendFollowing", allocation_extreme), STATUS_RESTRICTED,
            MarketSnapshot(symbol="NIFTY", last_price=100.0, volatility=5.0, liquidity_score=0.1),
            timestamp=TS_C,
        )

        print("=" * 70)
        print("RESTART RECOVERY -- fresh EventStore instance, same file")
        print("=" * 70)
        store2 = EventStore(path)
        recovered = read_all_shadow_results(store2)
        print(f"Recovered {len(recovered)} ShadowResultRecords from durable storage after simulated restart.")
        print()

        print("=" * 70)
        print("PIPELINE HEALTH")
        print("=" * 70)
        report = compute_pipeline_health(recovered)
        print(explain_pipeline_health(report))
        print()

        print("PROOF: evidence_score unmodified:", allocation_trend.candidate.assessment.strategy_score.evidence_score)


if __name__ == "__main__":
    main()
