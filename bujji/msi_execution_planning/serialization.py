"""Execution Planning Engine serialization — Series 98. Pure dict
round-trip, mirrors every prior MSI package's convention."""
from __future__ import annotations

from typing import Any, Dict

from .models import (
    DependencyEdge, Explanation, ExecutionPlanAssessment, ExecutionStep, FailurePolicy,
    RecoveryPolicy, RollbackPolicy, TimeoutPolicy, ValidationGate,
)


def _leg_to_dict(leg) -> Dict[str, Any]:
    return {"role": leg.role, "option_type": leg.option_type, "strike": leg.strike, "expiry": leg.expiry,
            "side": leg.side, "ratio": leg.ratio}


def execution_step_to_dict(s: ExecutionStep) -> Dict[str, Any]:
    return {
        "stage_index": s.stage_index, "role_filter": s.role_filter, "legs": [_leg_to_dict(l) for l in s.legs],
        "verification_required": s.verification_required, "reasoning": list(s.reasoning),
    }


def dependency_edge_to_dict(e: DependencyEdge) -> Dict[str, Any]:
    return {"from_stage": e.from_stage, "to_stage": e.to_stage, "reason": e.reason}


def validation_gate_to_dict(g: ValidationGate) -> Dict[str, Any]:
    return {"name": g.name, "real_time_evaluable": g.real_time_evaluable, "passed": g.passed,
            "reasoning": list(g.reasoning)}


def failure_policy_to_dict(p: FailurePolicy) -> Dict[str, Any]:
    return {"failure_type": p.failure_type, "response": p.response, "reasoning": list(p.reasoning)}


def rollback_policy_to_dict(p: RollbackPolicy) -> Dict[str, Any]:
    return {"policy": p.policy, "reasoning": list(p.reasoning)}


def recovery_policy_to_dict(p: RecoveryPolicy) -> Dict[str, Any]:
    return {"policy": p.policy, "reasoning": list(p.reasoning)}


def timeout_policy_to_dict(p: TimeoutPolicy) -> Dict[str, Any]:
    return {"policy": p.policy, "reasoning": list(p.reasoning)}


def explanation_to_dict(e: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id, "why_this_sequence": list(e.why_this_sequence),
        "why_this_dependency": list(e.why_this_dependency),
        "why_this_recovery_plan": list(e.why_this_recovery_plan),
        "why_this_validation_order": list(e.why_this_validation_order), "schema_version": e.schema_version,
    }


def assessment_to_dict(a: ExecutionPlanAssessment) -> Dict[str, Any]:
    return {
        "plan_id": a.plan_id, "timestamp": a.timestamp, "position_assessment_id": a.position_assessment_id,
        "strategy_family": a.strategy_family, "execution_mode": a.execution_mode,
        "order_sequence": [_leg_to_dict(l) for l in a.order_sequence],
        "execution_steps": [execution_step_to_dict(s) for s in a.execution_steps],
        "dependency_graph": [dependency_edge_to_dict(e) for e in a.dependency_graph],
        "validation_steps": [validation_gate_to_dict(g) for g in a.validation_steps],
        "failure_policies": [failure_policy_to_dict(p) for p in a.failure_policies],
        "rollback_policy": rollback_policy_to_dict(a.rollback_policy),
        "recovery_policy": recovery_policy_to_dict(a.recovery_policy),
        "timeout_policy": timeout_policy_to_dict(a.timeout_policy),
        "estimated_orders": a.estimated_orders, "estimated_latency": a.estimated_latency,
        "explanation": explanation_to_dict(a.explanation), "provenance": a.provenance,
        "schema_version": a.schema_version,
    }
