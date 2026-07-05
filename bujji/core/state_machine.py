"""Finite state machine for the trading session.

The FSM enforces legal transitions and logs every one of them. No trading
logic lives here — it only guards *which* state we may move to next, keeping
the orchestrator free of nested-if spaghetti.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from .enums import State
from .logging_setup import log_event

# Legal forward transitions. A day flows strictly through these edges.
_TRANSITIONS: dict[State, set[State]] = {
    State.WAITING: {State.READY, State.DONE_FOR_DAY},
    State.READY: {State.CONFIRMED, State.DONE_FOR_DAY},
    State.CONFIRMED: {State.IN_POSITION, State.READY, State.DONE_FOR_DAY},
    State.IN_POSITION: {State.EXITING, State.DONE_FOR_DAY},
    State.EXITING: {State.DONE_FOR_DAY, State.IN_POSITION},
    State.DONE_FOR_DAY: set(),
}


class IllegalTransition(RuntimeError):
    """Raised when an undefined transition is attempted."""


class StateMachine:
    """Guards and records session state transitions."""

    def __init__(
        self,
        logger: logging.Logger,
        initial: State = State.WAITING,
        on_transition: Optional[Callable[[State, State], None]] = None,
    ) -> None:
        self._state = initial
        self._log = logger
        self._on_transition = on_transition

    @property
    def state(self) -> State:
        return self._state

    def can_transition(self, target: State) -> bool:
        return target in _TRANSITIONS[self._state]

    def transition(self, target: State, reason: str = "") -> None:
        if target == self._state:
            return
        if not self.can_transition(target):
            raise IllegalTransition(
                f"Illegal transition {self._state} -> {target}"
            )
        previous = self._state
        self._state = target
        log_event(
            self._log,
            "state_transition",
            from_state=previous.value,
            to_state=target.value,
            reason=reason,
        )
        if self._on_transition:
            self._on_transition(previous, target)

    def restore(self, state: State, reason: str = "recovery") -> None:
        """Force the FSM to a state during crash recovery (C1).

        This bypasses transition guards *by design* — after a restart the
        previous state is a fact to be reinstated, not a transition to be
        validated. It is audited like any transition and must only be used by
        the recovery path.
        """
        previous = self._state
        self._state = state
        log_event(
            self._log,
            "state_restored",
            from_state=previous.value,
            to_state=state.value,
            reason=reason,
        )

    def is_terminal(self) -> bool:
        return self._state is State.DONE_FOR_DAY
