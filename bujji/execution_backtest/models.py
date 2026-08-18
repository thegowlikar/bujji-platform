"""Phase 20.2 -- minimal backtest driver, pure data contracts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class BacktestTradeResult:
    side: str
    quantity: int
    multiplier: int
    entry_reference_price: float
    exit_reference_price: float
    entry_fill_price: Optional[float]
    exit_fill_price: Optional[float]
    entry_status: str
    exit_status: str
    theoretical_gross_pnl: Optional[float]
    fees: Optional[float]
    slippage_cost: Optional[float]
    net_pnl: Optional[float]
    execution_profile_name: str
    family: str
    window_minutes: int
    window_date: str


@dataclass(frozen=True)
class FamilyBacktestSummary:
    """Aggregate comparison, theoretical vs realistic, for one family
    at one window length under one execution profile. `n` counts only
    trades where BOTH legs filled (never a partial sum presented as
    the whole)."""
    family: str
    window_minutes: int
    execution_profile_name: str
    n: int
    n_rejected: int
    total_theoretical_gross_pnl: Optional[float]
    total_net_pnl: Optional[float]
    total_fees: Optional[float]
    total_slippage_cost: Optional[float]
    trades: Tuple[BacktestTradeResult, ...]
