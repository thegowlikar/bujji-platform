"""Execution Engine models — frozen, immutable instruction set.

Nothing here connects to a broker, builds a REST payload, calls an
SDK, or generates an order. `ExecutionInstructionSet` is an ordered,
abstract workflow -- steps a future Broker Adapter can implement
however it likes, for whatever broker it targets.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ExecutionInstructionSet:
    instruction_set_id: str
    status: str
    execution_intent: str
    abstract_actions: Tuple[str, ...]
    required_controls: Tuple[str, ...]
    blocking_conditions: Tuple[str, ...]
    execution_trace: str
    confidence: str
    plan_id: Optional[str]
    timestamp: str
    version: str
