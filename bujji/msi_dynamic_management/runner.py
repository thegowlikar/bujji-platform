"""bujji.msi_dynamic_management.runner — Series 109.

Batch/streaming wrapper running the full six-decision board for one
real position on one real day, given the caller's own already-computed
real upstream assessments. Pure orchestration of `engine.py`'s pure
functions -- computes nothing MSI/Lifecycle/Strategy Optimisation
already compute themselves.
"""
from __future__ import annotations

from typing import Any, Optional

from bujji.msi_strategy_optimization import engine as mso_engine

from . import engine
from .models import DynamicManagementBoard


def run_dynamic_management(
    *, lifecycle, strategy_family: str, short_strike_delta: Optional[float], dte: Optional[int],
    portfolio_delta_after: Optional[float], portfolio_vega_after: Optional[float],
    entry_volatility_regime: Optional[str], current_volatility_regime: Optional[str],
    timestamp: str,
) -> DynamicManagementBoard:
    """Deliverable 3/4/5 for one position/day. Internally computes the
    real Series 108 `RollAssessment` (frozen, unmodified) once, then
    builds all six independent decisions + the transition assessment
    from it -- never re-derives what Series 108 already computed."""
    roll108 = mso_engine.assess_roll(lifecycle, short_strike_delta=short_strike_delta, dte=dte, timestamp=timestamp)
    return engine.build_dynamic_management_board(
        lifecycle, strategy_family, short_strike_delta=short_strike_delta, dte=dte,
        portfolio_delta_after=portfolio_delta_after, portfolio_vega_after=portfolio_vega_after,
        entry_volatility_regime=entry_volatility_regime, current_volatility_regime=current_volatility_regime,
        roll108=roll108, timestamp=timestamp,
    )
