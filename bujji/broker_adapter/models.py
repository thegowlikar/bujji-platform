"""Broker Adapter models — frozen, immutable translation records.

Nothing here is an actual broker request: there is no strike, no
expiry, no quantity, no client order id, no REST payload, no
authentication token anywhere in these dataclasses. That data does not
exist anywhere in the Trading Brain's own outputs -- constructing it
here would mean fabricating it, which this adapter never does. See
docs/BROKER_ADAPTER_ARCHITECTURE.md for the full explanation of why
`BrokerExecutionRequest` names broker OPERATIONS, not broker ORDERS.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class TranslatedAction:
    source_action: str
    broker_operation: Optional[str]
    status: str
    failure_reason: Optional[str]


@dataclass(frozen=True)
class BrokerExecutionRequest:
    request_id: str
    broker: str
    broker_version: str
    execution_status: str
    translated_actions: Tuple[TranslatedAction, ...]
    failure_reasons: Tuple[str, ...]
    translation_trace: str
    instruction_set_id: Optional[str]
    adapter_version: str
    timestamp: str
