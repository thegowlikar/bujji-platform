"""Order Lifecycle -- BUJJI Options OS v3, Gate F.2 Part 4.

A SEPARATE, order-scoped transition guard -- NOT a second copy of
F.0's RuntimeStateMachine (that machine governs the whole session's
lifecycle; this one governs a single simulated order's own progress
through the exchange). Deliberately small and order-scoped, mirroring
F.0's transition-guard/publish shape at a much narrower scope, per the
explicit instruction: "Do NOT create another state machine. Reuse F.0
RuntimeStateMachine only for runtime lifecycle. Order lifecycle is
separate."

`ExecutionStage` is an internal simulation-detail enum -- the value
PaperBroker.place_order() actually RETURNS to callers is still
`bujji.core.enums.OrderStatus` (PENDING/FILLED/PARTIAL/REJECTED/
CANCELLED/UNKNOWN), completely unchanged, so the execution INTERFACE
never changes. This enum only tracks the finer-grained internal
progress a real order takes on its way there, for tracing/EventBus
purposes.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class ExecutionStage(str, Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


_TRANSITIONS = {
    ExecutionStage.CREATED: {ExecutionStage.SUBMITTED, ExecutionStage.REJECTED},
    ExecutionStage.SUBMITTED: {ExecutionStage.ACCEPTED, ExecutionStage.REJECTED},
    ExecutionStage.ACCEPTED: {ExecutionStage.PARTIALLY_FILLED, ExecutionStage.FILLED, ExecutionStage.REJECTED, ExecutionStage.CANCELLED},
    ExecutionStage.PARTIALLY_FILLED: {ExecutionStage.FILLED, ExecutionStage.CANCELLED},
    ExecutionStage.FILLED: set(),
    ExecutionStage.REJECTED: set(),
    ExecutionStage.CANCELLED: set(),
}


class IllegalExecutionStageTransition(Exception):
    """Raised on an undefined transition -- never silently coerced."""


class OrderLifecycleTracker:
    """One instance per simulated order. `publish_fn`, if supplied, is
    called with (previous_stage, new_stage) on every transition --
    the caller wires this to the existing EventBus if it wants
    lifecycle events published; this class never imports or
    constructs an EventBus itself, avoiding any hard coupling."""

    def __init__(self, client_order_id: str, publish_fn=None) -> None:
        self._client_order_id = client_order_id
        self._stage = ExecutionStage.CREATED
        self._publish_fn = publish_fn
        self._history = [ExecutionStage.CREATED]

    @property
    def stage(self) -> ExecutionStage:
        return self._stage

    @property
    def history(self):
        return tuple(self._history)

    def can_transition(self, target: ExecutionStage) -> bool:
        return target in _TRANSITIONS[self._stage]

    def transition(self, target: ExecutionStage, reason: str = "") -> None:
        if target == self._stage:
            return
        if not self.can_transition(target):
            raise IllegalExecutionStageTransition(
                f"order {self._client_order_id!r}: illegal transition {self._stage.value} -> {target.value}"
            )
        previous = self._stage
        self._stage = target
        self._history.append(target)
        if self._publish_fn is not None:
            self._publish_fn(self._client_order_id, previous, target, reason)

    def is_terminal(self) -> bool:
        return self._stage in (ExecutionStage.FILLED, ExecutionStage.REJECTED, ExecutionStage.CANCELLED)
