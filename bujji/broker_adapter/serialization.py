"""JSON round-trip for BrokerExecutionRequest / TranslatedAction."""
from __future__ import annotations

from typing import Any, Dict, List

from .models import BrokerExecutionRequest, TranslatedAction


def action_to_dict(a: TranslatedAction) -> Dict[str, Any]:
    return {
        "source_action": a.source_action,
        "broker_operation": a.broker_operation,
        "status": a.status,
        "failure_reason": a.failure_reason,
    }


def action_from_dict(d: Dict[str, Any]) -> TranslatedAction:
    return TranslatedAction(
        source_action=d["source_action"],
        broker_operation=d.get("broker_operation"),
        status=d["status"],
        failure_reason=d.get("failure_reason"),
    )


def request_to_dict(r: BrokerExecutionRequest) -> Dict[str, Any]:
    return {
        "request_id": r.request_id,
        "broker": r.broker,
        "broker_version": r.broker_version,
        "execution_status": r.execution_status,
        "translated_actions": [action_to_dict(a) for a in r.translated_actions],
        "failure_reasons": list(r.failure_reasons),
        "translation_trace": r.translation_trace,
        "instruction_set_id": r.instruction_set_id,
        "adapter_version": r.adapter_version,
        "timestamp": r.timestamp,
    }


def request_from_dict(d: Dict[str, Any]) -> BrokerExecutionRequest:
    actions: List[TranslatedAction] = [action_from_dict(a) for a in d["translated_actions"]]
    return BrokerExecutionRequest(
        request_id=d["request_id"],
        broker=d["broker"],
        broker_version=d["broker_version"],
        execution_status=d["execution_status"],
        translated_actions=tuple(actions),
        failure_reasons=tuple(d["failure_reasons"]),
        translation_trace=d["translation_trace"],
        instruction_set_id=d.get("instruction_set_id"),
        adapter_version=d["adapter_version"],
        timestamp=d["timestamp"],
    )
