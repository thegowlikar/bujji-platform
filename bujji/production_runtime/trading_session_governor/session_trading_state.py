"""Session Trading State -- BUJJI Options OS v3, Gate V1.1 Component 1.

A DELIBERATELY SEPARATE, fourth axis of state -- not a duplicate of any
existing state machine. This codebase already has three, each scoped
to a genuinely different concern:
  F.0 RuntimeStateMachine        -> SESSION RUNTIME lifecycle (is the
                                     process connected, is the market
                                     open) -- INITIALIZING..COMPLETE.
  F.2 OrderLifecycleTracker       -> ONE ORDER's own progress through
                                     the simulated exchange.
  F.3 PositionLifecycleTracker    -> ONE POSITION GROUP's own progress
                                     (NEW..CLOSED).
  V1.1 SessionTradingStateTracker (this file) -> the DAY'S TRADING
                                     DISCIPLINE: has a strategy been
                                     chosen yet, has it been deployed,
                                     is the day done. None of the other
                                     three trackers can answer "has
                                     Bujji already made its one
                                     decision for today" -- that
                                     question belongs here alone.

Mirrors the exact transition-guard/publish-hook shape already
established by OrderLifecycleTracker/PositionLifecycleTracker -- same
discipline, new scope, never a generic reusable framework.
"""
from __future__ import annotations

from enum import Enum


class TradingSessionState(str, Enum):
    INITIALIZING = "INITIALIZING"
    ANALYSING_MARKET = "ANALYSING_MARKET"
    STRATEGY_LOCKED = "STRATEGY_LOCKED"
    POSITION_ACTIVE = "POSITION_ACTIVE"
    MANAGING = "MANAGING"
    EXITED = "EXITED"
    SESSION_COMPLETE = "SESSION_COMPLETE"


_TRANSITIONS = {
    TradingSessionState.INITIALIZING: {TradingSessionState.ANALYSING_MARKET},
    # ANALYSING_MARKET -> SESSION_COMPLETE directly is a REAL, legitimate
    # path: "fail closed, no trade today" (regime unclear, no strategy
    # fit the mapping table) is a disciplined outcome, not a failure --
    # a professional desk that sits out an unclear day never locks a
    # strategy at all.
    TradingSessionState.ANALYSING_MARKET: {TradingSessionState.STRATEGY_LOCKED, TradingSessionState.SESSION_COMPLETE},
    TradingSessionState.STRATEGY_LOCKED: {TradingSessionState.POSITION_ACTIVE, TradingSessionState.SESSION_COMPLETE},
    # POSITION_ACTIVE/MANAGING -> SESSION_COMPLETE directly is ALSO a real,
    # legitimate path: F.5's own EOD reconciliation contract is "report
    # unresolved positions, never force-close" -- if no hard exit condition
    # ever fired (found missing during the pre-Monday dry rehearsal: a day
    # that never triggers the mandatory-exit-time/profit/loss limits, and
    # D.4 never independently recommends closing to zero, has nowhere
    # legal to go), the session must still be able to end and be reported,
    # never raise. Ending the day with an unresolved position is a fact to
    # record, not a state-machine violation.
    TradingSessionState.POSITION_ACTIVE: {
        TradingSessionState.MANAGING, TradingSessionState.EXITED, TradingSessionState.SESSION_COMPLETE,
    },
    TradingSessionState.MANAGING: {TradingSessionState.EXITED, TradingSessionState.SESSION_COMPLETE},
    TradingSessionState.EXITED: {TradingSessionState.SESSION_COMPLETE},
    TradingSessionState.SESSION_COMPLETE: set(),
}


class IllegalTradingSessionTransition(Exception):
    """Raised on an undefined transition -- never silently coerced."""


class SessionTradingStateTracker:
    def __init__(self, publish_fn=None) -> None:
        self._state = TradingSessionState.INITIALIZING
        self._publish_fn = publish_fn

    @property
    def state(self) -> TradingSessionState:
        return self._state

    def can_transition(self, target: TradingSessionState) -> bool:
        return target in _TRANSITIONS[self._state]

    def transition(self, target: TradingSessionState, reason: str = "") -> None:
        if target == self._state:
            return
        if not self.can_transition(target):
            raise IllegalTradingSessionTransition(f"illegal transition {self._state.value} -> {target.value}")
        previous = self._state
        self._state = target
        if self._publish_fn is not None:
            self._publish_fn(previous, target, reason)

    def is_terminal(self) -> bool:
        return self._state == TradingSessionState.SESSION_COMPLETE
