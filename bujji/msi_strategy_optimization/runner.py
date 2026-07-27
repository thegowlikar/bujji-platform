"""bujji.msi_strategy_optimization.runner — Series 108.

Batch/streaming wrapper running the whole optimisation tail for one real
day given the caller's own already-computed real upstream assessments.
Never computes anything MSI/Strategy Selection/Position Construction/
Portfolio Construction/Margin Bridge/Lifecycle/Execution Planning
themselves already compute -- pure orchestration of the pure functions
in `engine.py`.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from . import engine


def run_optimization_tail(
    *, strategy_family: str, chain: Sequence, spot: Optional[float], expiry: Optional[str],
    t_years: Optional[float], as_of_date: str, timestamp: str,
    lifecycle: Optional[Any] = None, short_strike_delta: Optional[float] = None, dte: Optional[int] = None,
) -> Dict[str, Any]:
    """Runs Deliverables 3-8 for one real day's already-selected family.
    `lifecycle`/`short_strike_delta`/`dte` are optional -- when absent
    (e.g. a brand-new position with no lifecycle yet), roll/adjustment/
    conversion are honestly skipped (None), never fabricated against a
    position that doesn't exist yet."""
    strategy_opt = engine.optimize_strategy(strategy_family, timestamp=timestamp)
    strike_opt = None
    if chain and spot is not None and expiry is not None and t_years is not None:
        strike_opt = engine.optimize_strikes(
            strategy_family, chain, spot, expiry, t_years, strategy_opt.target_delta, timestamp=timestamp,
        )
    expiry_opt = engine.optimize_expiry(chain, spot, as_of_date, timestamp=timestamp) if chain else None

    roll = adjustment = conversion = None
    if lifecycle is not None:
        roll = engine.assess_roll(lifecycle, short_strike_delta=short_strike_delta, dte=dte, timestamp=timestamp)
        adjustment = engine.plan_adjustment(roll, lifecycle, timestamp=timestamp)
        conversion = engine.evaluate_conversion(strategy_family, roll, timestamp=timestamp)

    return {
        "strategy_optimization": strategy_opt, "strike_optimization": strike_opt,
        "expiry_optimization": expiry_opt, "roll": roll, "adjustment": adjustment, "conversion": conversion,
    }
