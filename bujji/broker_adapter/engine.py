"""Broker Adapter engine — BUJJI Options OS v3, Engineering Series 40,
Sprint 1 (FYERS v1).

This is the only module in the entire Trading Brain / execution stack
permitted to know a broker's name. It translates one already-produced,
broker-neutral `ExecutionInstructionSet` (Series 39) into a
broker-specific description of what operations that instruction set
implies -- never an actual broker call.

Deliberately does NOT import `bujji.broker` or `bujji.core.models`:
doing so would pull in the production `Broker` ABC's real
`connect`/`place_order` capability and, transitively, the FYERS SDK
dependency chain -- both explicitly forbidden this sprint. Instead,
this adapter reuses only the PRODUCTION `Broker` ABC's own METHOD-NAME
VOCABULARY (`connect`, `place_order`, `get_order`, `cancel_order` --
see bujji/broker/base.py) as the naming convention for its
action-to-operation translation table below, so that a future Runtime
Execution Service wiring this adapter to `bujji.broker.fyers.FyersBroker`
requires no renaming -- the names already match.

No REST payload, no order field, no strike, no expiry, no quantity, no
authentication, no retry, and no randomness appears anywhere in this
function.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from ..trading_brain.execution_engine.models import ExecutionInstructionSet
from . import taxonomy
from .models import BrokerExecutionRequest, TranslatedAction

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


# ---------------------------------------------------------------------------
# FYERS v1 action -> broker operation translation table. Every value
# here is a NAME, never a callable, never an SDK reference. Naming
# deliberately mirrors bujji.broker.base.Broker's own abstract method
# names (`connect`, `place_order`) wherever a real future correspondence
# exists.
# ---------------------------------------------------------------------------
FYERS_ACTION_MAP: Dict[str, str] = {
    "VALIDATE_PLAN": "VALIDATE_ORDER_PREREQUISITES",
    "VALIDATE_CONTROLS": "VALIDATE_ORDER_CONTROLS",
    "AUTHORIZE_EXECUTION": "PREPARE_PLACE_ORDER",
    # WAIT_FOR_ADAPTER intentionally has NO broker operation -- see
    # _translate_action()'s special case below. It always yields
    # TRANSLATION_STATUS_PENDING, never a broker_operation name.
}

# Registry keyed by broker name. Adding a broker means adding one
# entry here -- engine.py's translate() logic never changes.
ADAPTER_REGISTRY: Dict[str, Dict[str, str]] = {
    taxonomy.BROKER_FYERS: FYERS_ACTION_MAP,
}

ADAPTER_VERSION_BY_BROKER: Dict[str, str] = {
    taxonomy.BROKER_FYERS: "1.0.0",
}


def _translate_action(action: str, action_map: Dict[str, str]) -> TranslatedAction:
    if action == "WAIT_FOR_ADAPTER":
        return TranslatedAction(
            source_action=action,
            broker_operation=None,
            status=taxonomy.TRANSLATION_STATUS_PENDING,
            failure_reason=None,
        )
    if action == "BLOCK":
        return TranslatedAction(
            source_action=action,
            broker_operation=None,
            status=taxonomy.TRANSLATION_STATUS_PENDING,
            failure_reason=None,
        )
    if action in action_map:
        return TranslatedAction(
            source_action=action,
            broker_operation=action_map[action],
            status=taxonomy.TRANSLATION_STATUS_TRANSLATED,
            failure_reason=None,
        )
    return TranslatedAction(
        source_action=action,
        broker_operation=None,
        status=taxonomy.TRANSLATION_STATUS_FAILED,
        failure_reason=taxonomy.FAILURE_REASON_UNKNOWN_ACTION,
    )


def _request_id(
    instruction_set: Optional[ExecutionInstructionSet], broker: str, status: str, timestamp: str
) -> str:
    seed = "|".join(
        [instruction_set.instruction_set_id if instruction_set else "NONE", broker, status, timestamp]
    )
    return "BER-" + hashlib.md5(seed.encode()).hexdigest()[:16]


def _trace(source_status: str, broker: str, actions: Tuple[TranslatedAction, ...], status: str) -> str:
    action_summary = ", ".join(f"{a.source_action}->{a.broker_operation or a.status}" for a in actions)
    return (
        f"Instruction Set Status = {source_status}. "
        f"Broker = {broker}. "
        f"Translated Actions = {action_summary if action_summary else 'none'}. "
        f"Execution Status = {status}."
    )


def translate(
    instruction_set: Optional[ExecutionInstructionSet],
    broker: str = taxonomy.BROKER_FYERS,
    clock: Clock = _real_clock,
) -> BrokerExecutionRequest:
    """Translate one ExecutionInstructionSet into a broker-specific
    description of the operations it implies.

    Never connects to a broker, never authenticates, never submits an
    order, and never fabricates a strike, expiry, quantity, or client
    order id -- this adapter only names, for each abstract action
    already produced upstream, which broker operation it corresponds
    to (or why it could not be translated).
    """
    timestamp = clock().isoformat()

    # Unsupported broker -- checked before inspecting the instruction
    # set at all, since no translation table exists to apply either
    # way.
    if broker not in ADAPTER_REGISTRY:
        status = taxonomy.EXECUTION_STATUS_FAILED
        return BrokerExecutionRequest(
            request_id=_request_id(instruction_set, broker, status, timestamp),
            broker=broker,
            broker_version="UNKNOWN",
            execution_status=status,
            translated_actions=(),
            failure_reasons=(taxonomy.FAILURE_REASON_UNSUPPORTED_BROKER,),
            translation_trace=_trace(
                instruction_set.status if instruction_set else "NONE", broker, (), status
            ),
            instruction_set_id=instruction_set.instruction_set_id if instruction_set else None,
            adapter_version=taxonomy.BROKER_ADAPTER_VERSION,
            timestamp=timestamp,
        )

    broker_version = ADAPTER_VERSION_BY_BROKER[broker]
    action_map = ADAPTER_REGISTRY[broker]

    # Rule: nothing usable to translate at all.
    if instruction_set is None or instruction_set.status == "UNKNOWN":
        status = taxonomy.EXECUTION_STATUS_UNKNOWN
        source_status = instruction_set.status if instruction_set else "NONE"
        return BrokerExecutionRequest(
            request_id=_request_id(instruction_set, broker, status, timestamp),
            broker=broker,
            broker_version=broker_version,
            execution_status=status,
            translated_actions=(),
            failure_reasons=(taxonomy.FAILURE_REASON_INVALID_REQUEST,),
            translation_trace=_trace(source_status, broker, (), status),
            instruction_set_id=instruction_set.instruction_set_id if instruction_set else None,
            adapter_version=taxonomy.BROKER_ADAPTER_VERSION,
            timestamp=timestamp,
        )

    # Defensive: this adapter never trusts an upstream object blindly.
    # A request this adapter cannot correlate back to its source plan
    # is refused rather than translated anonymously.
    if not instruction_set.instruction_set_id:
        status = taxonomy.EXECUTION_STATUS_FAILED
        return BrokerExecutionRequest(
            request_id=_request_id(instruction_set, broker, status, timestamp),
            broker=broker,
            broker_version=broker_version,
            execution_status=status,
            translated_actions=(),
            failure_reasons=(taxonomy.FAILURE_REASON_MISSING_FIELD,),
            translation_trace=_trace(instruction_set.status, broker, (), status),
            instruction_set_id=None,
            adapter_version=taxonomy.BROKER_ADAPTER_VERSION,
            timestamp=timestamp,
        )

    # Rule: the instruction set itself was BLOCKED upstream -- honestly
    # relay that, translating nothing into a real operation.
    if instruction_set.status == "BLOCKED":
        translated = tuple(_translate_action(a, action_map) for a in instruction_set.abstract_actions)
        status = taxonomy.EXECUTION_STATUS_BLOCKED
        return BrokerExecutionRequest(
            request_id=_request_id(instruction_set, broker, status, timestamp),
            broker=broker,
            broker_version=broker_version,
            execution_status=status,
            translated_actions=translated,
            failure_reasons=(),
            translation_trace=_trace(instruction_set.status, broker, translated, status),
            instruction_set_id=instruction_set.instruction_set_id,
            adapter_version=taxonomy.BROKER_ADAPTER_VERSION,
            timestamp=timestamp,
        )

    # Rule: READY_FOR_ADAPTER -- translate every abstract action.
    translated_list: List[TranslatedAction] = [
        _translate_action(a, action_map) for a in instruction_set.abstract_actions
    ]
    translated = tuple(translated_list)

    failed = [t for t in translated if t.status == taxonomy.TRANSLATION_STATUS_FAILED]
    succeeded_or_pending = [
        t
        for t in translated
        if t.status in (taxonomy.TRANSLATION_STATUS_TRANSLATED, taxonomy.TRANSLATION_STATUS_PENDING)
    ]

    if not translated:
        status = taxonomy.EXECUTION_STATUS_UNKNOWN
        failure_reasons: Tuple[str, ...] = (taxonomy.FAILURE_REASON_INVALID_REQUEST,)
    elif failed and succeeded_or_pending:
        status = taxonomy.EXECUTION_STATUS_PARTIALLY_TRANSLATED
        failure_reasons = tuple(sorted({t.failure_reason for t in failed if t.failure_reason}))
    elif failed and not succeeded_or_pending:
        status = taxonomy.EXECUTION_STATUS_FAILED
        failure_reasons = tuple(sorted({t.failure_reason for t in failed if t.failure_reason}))
    else:
        status = taxonomy.EXECUTION_STATUS_TRANSLATED
        failure_reasons = ()

    return BrokerExecutionRequest(
        request_id=_request_id(instruction_set, broker, status, timestamp),
        broker=broker,
        broker_version=broker_version,
        execution_status=status,
        translated_actions=translated,
        failure_reasons=failure_reasons,
        translation_trace=_trace(instruction_set.status, broker, translated, status),
        instruction_set_id=instruction_set.instruction_set_id,
        adapter_version=taxonomy.BROKER_ADAPTER_VERSION,
        timestamp=timestamp,
    )
