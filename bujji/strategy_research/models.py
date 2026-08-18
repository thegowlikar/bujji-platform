"""Phase 20.3 -- pure data contracts. No IO, no broker, no execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple

ATTRIBUTION_A_MIC_CORRECT_STRATEGY_WORKED = "A_MIC_CORRECT_STRATEGY_WORKED"
ATTRIBUTION_B_MIC_CORRECT_STRATEGY_FAILED = "B_MIC_CORRECT_STRATEGY_FAILED"
ATTRIBUTION_C_MIC_WRONG_STRATEGY_WORKED = "C_MIC_WRONG_STRATEGY_WORKED"
ATTRIBUTION_D_MIC_WRONG_STRATEGY_FAILED = "D_MIC_WRONG_STRATEGY_FAILED"
ALL_ATTRIBUTION_QUADRANTS = (
    ATTRIBUTION_A_MIC_CORRECT_STRATEGY_WORKED, ATTRIBUTION_B_MIC_CORRECT_STRATEGY_FAILED,
    ATTRIBUTION_C_MIC_WRONG_STRATEGY_WORKED, ATTRIBUTION_D_MIC_WRONG_STRATEGY_FAILED,
)


@dataclass(frozen=True)
class StrategyFamily:
    """A Cycle-1 strategy family. Deliberately thin: every method is a
    reference to an ALREADY-EXISTING, UNMODIFIED function (Phase 20.2's
    `execution_backtest.driver`) -- this class adds an explicit,
    declarative interface over that logic, not new strategy logic.
    No order placement, no broker dependency anywhere in this type."""

    name: str
    market_conditions_required: Tuple[str, ...]     # intraday regime labels this family may trade.
    incompatible_conditions: Tuple[str, ...]         # intraday regime labels this family must NEVER trade.
    # (window_n, window_n1) -> (side, entry, exit) or None. Direction MUST be
    # decided from window_n (the window MIC classified, fully known before
    # window_n1 begins) -- window_n1 supplies ONLY the entry/exit reference
    # prices actually transacted, never information used to decide direction.
    # See `strategy_research.signals` module docstring for why this two-
    # window contract exists (the lookahead fix).
    generate_signal: Callable[[Sequence, Sequence], Optional[Tuple[str, float, float]]]
    simulate_position: Callable[..., object]          # delegates to simulate_round_trip_trade, unmodified.
    explain_reason: Callable[[object], str]           # IntradayWindowReading -> one-line evidence string.

    def __post_init__(self):
        overlap = set(self.market_conditions_required) & set(self.incompatible_conditions)
        if overlap:
            raise ValueError(f"{self.name}: a regime cannot be both required and incompatible: {overlap}")

    def is_eligible_for(self, regime: str) -> bool:
        return regime in self.market_conditions_required and regime not in self.incompatible_conditions


@dataclass(frozen=True)
class AttributedTrade:
    """One Cycle-1 research trade with full attribution -- the
    mandatory Phase 20.3 record. `mic_state`/`selected_family`/
    `reason` answer "what did MIC say and why"; `outcome_net_pnl`
    answers "what happened after realistic execution costs";
    `attribution` answers "was the CALL right, independently of
    whether the trade made money" (see `strategy_research.attribution`
    module docstring for exactly how MIC-correct/wrong is measured
    without lookahead).

    Phase 20.4 adds `forecast_correct`: distinct from `outcome_worked`
    (net, AFTER costs), this is the strategy's own directional call
    (price moved the predicted way) measured on THEORETICAL gross P&L
    -- BEFORE costs. Separating these two answers this phase's own
    Question 3 directly: `forecast_correct is True and outcome_worked
    is False` means execution destroyed a real edge; `forecast_correct
    is False` means there was never an edge for costs to destroy."""

    date: str
    window_minutes: int
    mic_state: str                    # the regime MIC classified window N as.
    selected_family: str              # the StrategyFamily.name selected for that state, or "NO_TRADE".
    reason: str                       # explain_reason() output -- ER/ADX/VOL evidence, human-readable.
    realized_next_window_state: Optional[str]   # window N+1's OWN independently-classified regime.
    mic_correct: Optional[bool]       # None if no trade was taken (NO_TRADE state).
    theoretical_gross_pnl: Optional[float]
    net_pnl: Optional[float]
    execution_drag: Optional[float]   # theoretical_gross_pnl - net_pnl.
    forecast_correct: Optional[bool]  # theoretical_gross_pnl > 0 -- direction right, BEFORE costs. None if no trade.
    outcome_worked: Optional[bool]    # net_pnl > 0 -- profitable AFTER costs. None if no trade.
    attribution: Optional[str]        # one of ALL_ATTRIBUTION_QUADRANTS, or None if no trade was taken.
