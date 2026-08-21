"""Entry Control -- BUJJI Options OS v3, Gate V1.1 Component 4.

Answers exactly one question -- "may the ONE entry pipeline run at all
right now?" -- and nothing about whether the trade itself is good; the
Risk Governor remains the sole authority for that, called unmodified,
downstream of this gate. Pure boolean logic over the session's own
trading-discipline state and lock -- no market/risk data referenced.

Entry is allowed in EXACTLY one window: STRATEGY_LOCKED -- the strategy
has been chosen for the day but not yet deployed. Before that
(ANALYSING_MARKET), there is nothing locked to deploy yet. After that
(POSITION_ACTIVE/MANAGING/EXITED), the day's one deployment has already
happened -- STRATEGY_ALREADY_DEPLOYED, not "strategy is locked," is the
condition that blocks a second entry; being locked is a PRECONDITION
of the single allowed entry, not a reason to block it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from .session_trading_state import TradingSessionState
from .strategy_lock import StrategyLock

REASON_SESSION_COMPLETE = "SESSION_COMPLETE"
REASON_STRATEGY_ALREADY_DEPLOYED = "STRATEGY_ALREADY_DEPLOYED"
REASON_WRONG_STATE = "WRONG_STATE"
REASON_ALLOWED = "ALLOWED"


@dataclass(frozen=True)
class EntryControlDecision:
    allowed: bool
    reason: str


_ALREADY_DEPLOYED_STATES = (
    TradingSessionState.POSITION_ACTIVE, TradingSessionState.MANAGING, TradingSessionState.EXITED,
)


def can_enter_trade(session_state: TradingSessionState, strategy_lock: StrategyLock) -> EntryControlDecision:
    if session_state == TradingSessionState.SESSION_COMPLETE:
        return EntryControlDecision(False, REASON_SESSION_COMPLETE)
    if session_state in _ALREADY_DEPLOYED_STATES:
        return EntryControlDecision(False, REASON_STRATEGY_ALREADY_DEPLOYED)
    if session_state == TradingSessionState.STRATEGY_LOCKED and strategy_lock.is_locked():
        return EntryControlDecision(True, REASON_ALLOWED)
    return EntryControlDecision(False, f"{REASON_WRONG_STATE}:{session_state.value}")
