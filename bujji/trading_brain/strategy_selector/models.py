"""Strategy Selector models — frozen, immutable decision records.

Nothing here sizes a position, calculates lots, places an order, or
estimates PnL. `StrategyDecision` names, at most, ONE existing
strategy from the registry -- never invents one, never ranks a
portfolio of them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class StrategyEvaluation:
    """One strategy's eligibility verdict against today's market."""

    strategy_id: str
    eligibility: str
    supporting_conditions: Tuple[str, ...]
    rejecting_conditions: Tuple[str, ...]


@dataclass(frozen=True)
class StrategyDecision:
    decision_id: str
    selected_strategy: Optional[str]
    selection_status: str
    selection_confidence: str
    selection_reason: str
    supporting_conditions: Tuple[str, ...]
    rejecting_conditions: Tuple[str, ...]
    alternative_candidates: Tuple[str, ...]
    all_evaluations: Tuple[StrategyEvaluation, ...]
    decision_trace: str
    market_state_assessment_id: Optional[str]
    timestamp: str
    version: str
