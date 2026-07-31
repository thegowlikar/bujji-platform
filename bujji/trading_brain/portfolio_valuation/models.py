"""Portfolio Valuation models — Live Shadow Real-Time Paper Execution
sprint. Frozen, immutable records.

This module computes nothing itself (see engine.py); these are plain
data carriers. `PortfolioValuation` is deliberately re-computed fresh
on every call, never mutated in place, so replaying the exact same
tick sequence twice produces byte-identical results -- the same
determinism discipline used throughout this codebase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class LegValuation:
    symbol: str
    side: str                     # "BUY" | "SELL", verbatim from the position ledger.
    quantity: int
    entry_price: float
    entry_timestamp: Optional[str]
    current_price: Optional[float]   # None when no tick has ever been observed for this symbol.
    tick_timestamp: Optional[str]    # Timestamp of the tick that produced current_price.
    unrealized_pnl: Optional[float]  # None when current_price is None -- never fabricated as 0.
    price_is_stale: bool             # True when current_price came from a prior tick, not this revaluation's own tick.


@dataclass(frozen=True)
class PortfolioValuation:
    valuation_id: str
    as_of: str
    legs: Tuple[LegValuation, ...]
    total_realized_pnl: float
    total_unrealized_pnl: Optional[float]   # None if ANY leg has no observed price -- never partially summed and presented as complete.
    total_pnl: Optional[float]              # realized + unrealized; None under the same condition as total_unrealized_pnl.
    triggering_symbol: Optional[str]        # The symbol whose tick triggered this revaluation, if any (None for an on-demand/non-tick call).
    triggering_tick_timestamp: Optional[str]
    version: str = "1.0.0"
