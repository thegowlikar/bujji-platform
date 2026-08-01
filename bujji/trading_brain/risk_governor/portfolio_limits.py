"""Portfolio-level numeric limits — BUJJI Options OS v3, Numeric Risk
Governor.

Counts and bounds are computed over STRATEGY POSITIONS (distinct
`position_group_id`s), never individual legs -- a multi-leg spread is
one position, matching the original Numeric Risk Governor requirement
("position limits count strategy positions or portfolio groups, not
individual option legs").

Pure functions only. Exposure figures are caller-supplied (this module
never re-derives them from contracts/orders itself, same decoupled-
input pattern as defined_risk.py) -- a missing exposure entry for an
active group fails closed, never assumes zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional

from bujji.trading_brain.risk_governor.position_group_fold import (
    LIFECYCLE_OPEN,
    LIFECYCLE_PARTIALLY_OPEN,
    PositionGroupState,
)

Clock = Callable[[], datetime]

_ACTIVE_LIFECYCLE_STATES = (LIFECYCLE_OPEN, LIFECYCLE_PARTIALLY_OPEN)


@dataclass(frozen=True)
class PortfolioLimits:
    max_simultaneous_positions: int
    max_concentration_per_underlying: float   # fraction of total gross exposure, e.g. 0.40


@dataclass(frozen=True)
class PortfolioLimitAssessment:
    decision: str                    # "ALLOW" | "VETO"
    blocking_reason: Optional[str]
    active_position_count: int
    concentration_by_underlying: Dict[str, float]   # {} whenever decision == "VETO" on a data-trust reason
    evaluated_at: datetime


def _early(reason: str, active_position_count: int, clock: Clock) -> PortfolioLimitAssessment:
    return PortfolioLimitAssessment(
        decision="VETO", blocking_reason=reason, active_position_count=active_position_count,
        concentration_by_underlying={}, evaluated_at=clock(),
    )


def assess_portfolio_limits(
    group_states: List[PositionGroupState],
    exposure_by_position_group_id: Dict[str, float],
    limits: PortfolioLimits,
    clock: Clock,
) -> PortfolioLimitAssessment:
    active_groups = [s for s in group_states if s.lifecycle_state in _ACTIVE_LIFECYCLE_STATES]
    active_position_count = len({s.position_group_id for s in active_groups})

    if active_position_count > limits.max_simultaneous_positions:
        return _early("MAX_SIMULTANEOUS_POSITIONS_EXCEEDED", active_position_count, clock)

    if not active_groups:
        return PortfolioLimitAssessment(
            decision="ALLOW", blocking_reason=None, active_position_count=0,
            concentration_by_underlying={}, evaluated_at=clock(),
        )

    exposures: Dict[str, float] = {}
    for state in active_groups:
        exposure = exposure_by_position_group_id.get(state.position_group_id)
        if exposure is None:
            return _early("EXPOSURE_DATA_MISSING", active_position_count, clock)
        if exposure < 0:
            return _early("EXPOSURE_DATA_INVALID_NEGATIVE", active_position_count, clock)
        if state.underlying is None:
            return _early("UNDERLYING_UNRESOLVED", active_position_count, clock)
        exposures[state.underlying] = exposures.get(state.underlying, 0.0) + exposure

    total_exposure = sum(exposures.values())
    if total_exposure == 0:
        return _early("EXPOSURE_UNTRUSTED_ZERO_TOTAL", active_position_count, clock)

    concentration_by_underlying = {u: v / total_exposure for u, v in exposures.items()}
    for underlying, concentration in concentration_by_underlying.items():
        if concentration > limits.max_concentration_per_underlying:
            return PortfolioLimitAssessment(
                decision="VETO", blocking_reason=f"CONCENTRATION_EXCEEDED:{underlying}",
                active_position_count=active_position_count,
                concentration_by_underlying=concentration_by_underlying, evaluated_at=clock(),
            )

    return PortfolioLimitAssessment(
        decision="ALLOW", blocking_reason=None, active_position_count=active_position_count,
        concentration_by_underlying=concentration_by_underlying, evaluated_at=clock(),
    )
