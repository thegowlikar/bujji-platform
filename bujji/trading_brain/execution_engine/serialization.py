"""JSON round-trip for ExecutionInstructionSet."""
from __future__ import annotations

from typing import Any, Dict

from .models import ExecutionInstructionSet


def instruction_set_to_dict(i: ExecutionInstructionSet) -> Dict[str, Any]:
    return {
        "instruction_set_id": i.instruction_set_id,
        "status": i.status,
        "execution_intent": i.execution_intent,
        "abstract_actions": list(i.abstract_actions),
        "required_controls": list(i.required_controls),
        "blocking_conditions": list(i.blocking_conditions),
        "execution_trace": i.execution_trace,
        "confidence": i.confidence,
        "plan_id": i.plan_id,
        "timestamp": i.timestamp,
        "version": i.version,
    }


def instruction_set_from_dict(d: Dict[str, Any]) -> ExecutionInstructionSet:
    return ExecutionInstructionSet(
        instruction_set_id=d["instruction_set_id"],
        status=d["status"],
        execution_intent=d["execution_intent"],
        abstract_actions=tuple(d["abstract_actions"]),
        required_controls=tuple(d["required_controls"]),
        blocking_conditions=tuple(d["blocking_conditions"]),
        execution_trace=d["execution_trace"],
        confidence=d["confidence"],
        plan_id=d.get("plan_id"),
        timestamp=d["timestamp"],
        version=d["version"],
    )
