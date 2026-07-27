"""Strategy Evaluation — Production Pipeline Stage 3 (entry-gating half).

Production Engineering Sprint 5 (Stage Interface Extraction). This is an
EXTRACTION, not a rewrite: the three-check ordering here (is_trade, then
max-trades-per-day, then clock trust) and the resulting action for each
outcome are identical to what previously ran inline inside
Orchestrator._handle_pre_position(). No check reordered, no new gate
introduced.

Pure and side-effect-free: takes only the four boolean/int inputs the
original checks used, and returns an EntryGateDecision. It never
transitions the FSM, never logs, never touches the event bus, never calls
_enter() -- all of that stays in Orchestrator._handle_pre_position(),
which performs the exact same side effects as before, chosen by
branching on this function's result. This is what makes the entry-gating
decision independently unit-testable without constructing a live
Orchestrator/FSM.

NOTE: the ORB-readiness transition (WAITING -> READY) at the top of
_handle_pre_position() is intentionally NOT part of this extraction --
it is unconditional (runs every candle regardless of signal.is_trade)
and reads/writes FSM state directly, unlike the three checks below which
form a single sequential gate. Splitting it out was judged to add
surface area without adding testability, per this sprint's narrow scope.
"""
from __future__ import annotations

from dataclasses import dataclass

NO_TRADE = "no_trade"
MAX_TRADES_REACHED = "max_trades_reached"
CLOCK_UNTRUSTED = "clock_untrusted"
PROCEED = "proceed"


@dataclass(frozen=True)
class EntryGateDecision:
    action: str  # One of NO_TRADE / MAX_TRADES_REACHED / CLOCK_UNTRUSTED / PROCEED.


def evaluate_entry_gate(
    is_trade: bool,
    trades_taken: int,
    max_trades_per_day: int,
    clock_trusted: bool,
) -> EntryGateDecision:
    if not is_trade:
        return EntryGateDecision(action=NO_TRADE)
    if trades_taken >= max_trades_per_day:
        return EntryGateDecision(action=MAX_TRADES_REACHED)
    if not clock_trusted:
        return EntryGateDecision(action=CLOCK_UNTRUSTED)
    return EntryGateDecision(action=PROCEED)
