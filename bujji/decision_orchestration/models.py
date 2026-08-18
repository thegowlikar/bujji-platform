"""Phase 20.10 -- pure data contracts. No IO, no broker, no execution,
no quantity/order/capital-amount vocabulary anywhere in this module.

`DecisionState` is this module's vocabulary of `FinalDecision.
decision_state` values -- plain string constants, following this
codebase's own established convention (`bujji.mic_v0.models`,
`bujji.epistemics.uncertainty`, and every prior phase in this Cycle
all do the same), validated in `FinalDecision.__post_init__` rather
than expressed as a separate `enum.Enum` type.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from bujji.capital_intelligence import AllocationAssessment
from bujji.opportunity_portfolio import PortfolioDecision

EXECUTABLE_CANDIDATE = "EXECUTABLE_CANDIDATE"
WATCH = "WATCH"
NO_OPPORTUNITY = "NO_OPPORTUNITY"
BLOCKED = "BLOCKED"
INSUFFICIENT_INTELLIGENCE = "INSUFFICIENT_INTELLIGENCE"
ALL_DECISION_STATES = (EXECUTABLE_CANDIDATE, WATCH, NO_OPPORTUNITY, BLOCKED, INSUFFICIENT_INTELLIGENCE)


@dataclass(frozen=True)
class FinalDecision:
    """The composed, final intelligence verdict for ONE strategy.
    `allocation`/`portfolio_decision` are carried through verbatim
    from Phase 20.8/20.9 -- `None` only when genuinely absent (the
    `INSUFFICIENT_INTELLIGENCE` case), never a stand-in for a bad
    answer that WAS actually computed."""

    strategy_name: Optional[str]
    decision_state: str
    positive: Tuple[str, ...]
    negative: Tuple[str, ...]
    unknown: Tuple[str, ...]
    allocation: Optional[AllocationAssessment]
    portfolio_decision: Optional[PortfolioDecision]

    def __post_init__(self):
        if self.decision_state not in ALL_DECISION_STATES:
            raise ValueError(f"unknown decision state {self.decision_state!r}")
        if not self.positive and not self.negative and not self.unknown:
            raise ValueError("a FinalDecision must always carry at least one reason -- never a bare verdict")
