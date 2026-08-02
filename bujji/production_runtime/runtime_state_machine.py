"""Runtime State Machine -- BUJJI Options OS v3, Gate F.0.

PURPOSE: one authoritative state every later Gate F component reads
instead of inventing its own flags. Pure infrastructure -- no trading
logic, no market logic, no broker logic. This module never decides
whether to enter/exit/resize/hedge/reject; it only tracks and guards
WHICH runtime phase the session is currently in, and publishes that
change so anything else (dashboard, journal, Trading Brain
components) can react without polling.

STEP 1 FINDING -- A DIFFERENT, PRE-EXISTING STATE MACHINE ALREADY
EXISTS, DELIBERATELY NOT REUSED: `bujji.core.state_machine.
StateMachine` + `bujji.core.enums.State` (WAITING/READY/CONFIRMED/
IN_POSITION/EXITING/DONE_FOR_DAY) is real, already-wired
infrastructure -- but it is exclusively used by `bujji.core.
orchestrator.Orchestrator`, the LEGACY single-strategy ORB-breakout
runtime this Gate F work was explicitly told not to extend. Its state
vocabulary is coarser and strategy-specific by design (CONFIRMED's own
docstring: "Signal generated, order being placed" -- ORB-specific
language) and structurally assumes a single position (IN_POSITION is
singular). Per this project's established discipline ("if multiple
producers already exist for the same concept, STOP, document the
conflict, do not merge"), this module is a deliberately SEPARATE state
machine for the NEW production_runtime-based Live Shadow Runtime, not
an extension or reuse of the legacy one. The two will coexist while
the legacy runtime continues to exist, exactly as scoped.

EVENT BUS REUSE: `bujji.core.event_bus.EventBus` already defines
`EventType.STATE_CHANGED` in its own enum -- this module publishes
through that existing event type, introducing no new messaging layer
and no new EventType, per the explicit instruction to reuse the
existing bus rather than add another one.
"""
from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

from bujji.core.event_bus import Event, EventBus, EventType

Clock = Callable[[], datetime]


class RuntimeState(str, Enum):
    INITIALIZING = "INITIALIZING"
    PREMARKET = "PREMARKET"
    CONNECTING = "CONNECTING"
    LIVE = "LIVE"
    ENTRY_ENABLED = "ENTRY_ENABLED"
    POSITION_ACTIVE = "POSITION_ACTIVE"
    MANAGING = "MANAGING"
    EXITING = "EXITING"
    POSTMARKET = "POSTMARKET"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"


# Legal forward transitions. ERROR is reachable from every non-terminal
# state (a runtime fault can occur at any phase) and is itself
# terminal -- once ERROR, only a fresh session (a new RuntimeStateMachine)
# resumes, never a transition out of it. COMPLETE is the sole
# non-error terminal state.
_NON_TERMINAL_STATES = (
    RuntimeState.INITIALIZING, RuntimeState.PREMARKET, RuntimeState.CONNECTING, RuntimeState.LIVE,
    RuntimeState.ENTRY_ENABLED, RuntimeState.POSITION_ACTIVE, RuntimeState.MANAGING,
    RuntimeState.EXITING, RuntimeState.POSTMARKET,
)

_TRANSITIONS: dict = {
    RuntimeState.INITIALIZING: {RuntimeState.PREMARKET, RuntimeState.ERROR},
    RuntimeState.PREMARKET: {RuntimeState.CONNECTING, RuntimeState.ERROR},
    RuntimeState.CONNECTING: {RuntimeState.LIVE, RuntimeState.ERROR},
    RuntimeState.LIVE: {RuntimeState.ENTRY_ENABLED, RuntimeState.POSTMARKET, RuntimeState.ERROR},
    RuntimeState.ENTRY_ENABLED: {RuntimeState.POSITION_ACTIVE, RuntimeState.POSTMARKET, RuntimeState.ERROR},
    # MANAGING loops back to POSITION_ACTIVE (managing one position cycle
    # is not itself a terminal phase -- the runtime returns to actively
    # holding while awaiting the next management tick) and can itself
    # newly open MORE positions while others are already active, so
    # POSITION_ACTIVE -> POSITION_ACTIVE is also legal (a self-transition,
    # handled by the no-op short-circuit below, never rejected).
    RuntimeState.POSITION_ACTIVE: {RuntimeState.MANAGING, RuntimeState.EXITING, RuntimeState.POSTMARKET, RuntimeState.ERROR},
    RuntimeState.MANAGING: {RuntimeState.POSITION_ACTIVE, RuntimeState.EXITING, RuntimeState.POSTMARKET, RuntimeState.ERROR},
    RuntimeState.EXITING: {RuntimeState.POSITION_ACTIVE, RuntimeState.ENTRY_ENABLED, RuntimeState.POSTMARKET, RuntimeState.ERROR},
    RuntimeState.POSTMARKET: {RuntimeState.COMPLETE, RuntimeState.ERROR},
    RuntimeState.COMPLETE: set(),
    RuntimeState.ERROR: set(),
}


class IllegalRuntimeTransition(RuntimeError):
    """Raised on an undefined transition -- never silently coerced or
    ignored."""


class RuntimeStateMachine:
    """Guards, records, and publishes runtime session state transitions.
    Contains no trading/market/broker logic of any kind -- it is read
    by everything, decided by nothing."""

    def __init__(
        self,
        event_bus: EventBus,
        clock: Clock,
        initial: RuntimeState = RuntimeState.INITIALIZING,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._state = initial
        self._event_bus = event_bus
        self._clock = clock
        self._log = logger

    @property
    def state(self) -> RuntimeState:
        return self._state

    def can_transition(self, target: RuntimeState) -> bool:
        return target in _TRANSITIONS[self._state]

    def transition(self, target: RuntimeState, reason: str = "") -> None:
        if target == self._state:
            return
        if not self.can_transition(target):
            raise IllegalRuntimeTransition(f"Illegal transition {self._state.value} -> {target.value}")
        previous = self._state
        self._state = target
        if self._log:
            self._log.info("runtime_state_transition from=%s to=%s reason=%s", previous.value, target.value, reason)
        self._event_bus.publish_nowait(Event(
            type=EventType.STATE_CHANGED,
            payload={"from_state": previous.value, "to_state": target.value, "reason": reason},
            timestamp=self._clock(),
        ))

    def is_terminal(self) -> bool:
        return self._state in (RuntimeState.COMPLETE, RuntimeState.ERROR)

    def is_error(self) -> bool:
        return self._state is RuntimeState.ERROR
