"""Phase 20.2 -- minimal execution-reality backtest driver.

Historical candles -> strategy signal -> Execution Simulator ->
ExecutionReport -> Net P&L. Answers exactly one question: "does
execution reality materially change theoretical results?" -- not
whether any strategy is profitable. See
docs/PHASE_20_2_EXECUTION_REALITY_REPORT.md.
"""
from .models import BacktestTradeResult, FamilyBacktestSummary
from .driver import run_family_backtest, simulate_round_trip_trade

__all__ = [
    "BacktestTradeResult", "FamilyBacktestSummary",
    "run_family_backtest", "simulate_round_trip_trade",
]
