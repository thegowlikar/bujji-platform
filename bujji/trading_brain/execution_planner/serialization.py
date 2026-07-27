"""JSON round-trip for ExecutionPlan / ExecutionStep."""
from __future__ import annotations

from typing import Any, Dict, List

from .models import ExecutionPlan, ExecutionStep


def step_to_dict(s: ExecutionStep) -> Dict[str, Any]:
    return {"step_type": s.step_type, "description": s.description}


def step_from_dict(d: Dict[str, Any]) -> ExecutionStep:
    return ExecutionStep(step_type=d["step_type"], description=d["description"])


def plan_to_dict(p: ExecutionPlan) -> Dict[str, Any]:
    return {
        "plan_id": p.plan_id,
        "status": p.status,
        "execution_intent": p.execution_intent,
        "strategy_id": p.strategy_id,
        "capital_intent": p.capital_intent,
        "required_controls": list(p.required_controls),
        "execution_constraints": list(p.execution_constraints),
        "execution_steps": [step_to_dict(s) for s in p.execution_steps],
        "confidence": p.confidence,
        "planning_trace": p.planning_trace,
        "capital_decision_id": p.capital_decision_id,
        "strategy_decision_id": p.strategy_decision_id,
        "timestamp": p.timestamp,
        "version": p.version,
    }


def plan_from_dict(d: Dict[str, Any]) -> ExecutionPlan:
    steps: List[ExecutionStep] = [step_from_dict(s) for s in d["execution_steps"]]
    return ExecutionPlan(
        plan_id=d["plan_id"],
        status=d["status"],
        execution_intent=d["execution_intent"],
        strategy_id=d.get("strategy_id"),
        capital_intent=d["capital_intent"],
        required_controls=tuple(d["required_controls"]),
        execution_constraints=tuple(d["execution_constraints"]),
        execution_steps=tuple(steps),
        confidence=d["confidence"],
        planning_trace=d["planning_trace"],
        capital_decision_id=d.get("capital_decision_id"),
        strategy_decision_id=d.get("strategy_decision_id"),
        timestamp=d["timestamp"],
        version=d["version"],
    )
