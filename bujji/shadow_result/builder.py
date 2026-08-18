"""Phase 20.20 -- pure composition. Reads only already-computed fields
from Phase 20.10/20.17.1/20.18/20.19's own real output objects -- never
recomputes evidence, confidence, risk status, or execution outcome.
"""
from __future__ import annotations

from typing import Optional

from bujji.decision_orchestration import BLOCKED, FinalDecision, NO_OPPORTUNITY
from bujji.risk_context_adapter import RiskContextAssessment, STATUS_NOT_EVALUATED
from bujji.execution_intelligence import ExecutionIntent, ExecutionPlan
from bujji.broker_boundary import BrokerResponse, ReconciliationResult

from .models import (
    STAGE_BROKER_ROUTED, STAGE_DECISION_ONLY, STAGE_EXECUTION_PLANNED, STAGE_RECONCILED, STAGE_RISK_EVALUATED,
    ShadowResultRecord, shadow_result_id_for,
)

_REJECTED_DECISION_STATES = (NO_OPPORTUNITY, BLOCKED)


def build_shadow_result_record(
    final_decision: FinalDecision,
    risk_assessment: Optional[RiskContextAssessment] = None,
    execution_intent: Optional[ExecutionIntent] = None,
    execution_plan: Optional[ExecutionPlan] = None,
    broker_response: Optional[BrokerResponse] = None,
    reconciliation_result: Optional[ReconciliationResult] = None,
    *,
    session_id: str,
    timestamp: str,
) -> ShadowResultRecord:
    strategy_name = final_decision.strategy_name or "UNKNOWN"

    stage = STAGE_DECISION_ONLY
    if risk_assessment is not None and risk_assessment.status != STATUS_NOT_EVALUATED:
        stage = STAGE_RISK_EVALUATED
    if execution_intent is not None and execution_plan is not None:
        stage = STAGE_EXECUTION_PLANNED
    if broker_response is not None:
        stage = STAGE_BROKER_ROUTED
    if reconciliation_result is not None:
        stage = STAGE_RECONCILED

    return ShadowResultRecord(
        record_id=shadow_result_id_for(strategy_name, timestamp),
        session_id=session_id, strategy_name=strategy_name, timestamp=timestamp,
        decision_state=final_decision.decision_state,
        pipeline_stage_reached=stage,
        risk_context_status=risk_assessment.status if risk_assessment is not None else None,
        execution_intent_created=execution_intent is not None,
        execution_simulation_required=execution_plan.simulation_required if execution_plan is not None else None,
        broker_response_status=broker_response.status if broker_response is not None else None,
        broker_adapter_name=broker_response.adapter_name if broker_response is not None else None,
        execution_result_status=(
            broker_response.fill_information.status
            if broker_response is not None and broker_response.fill_information is not None else None
        ),
        reconciliation_consistency=reconciliation_result.consistency if reconciliation_result is not None else None,
    )
