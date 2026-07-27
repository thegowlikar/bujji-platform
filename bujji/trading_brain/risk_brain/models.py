"""Risk Brain models — frozen, immutable risk verdict.

Nothing here predicts profit, sizes a position, changes a strategy, or
executes anything. `RiskAssessment` answers exactly one question:
should today's selected strategy be allowed to proceed?
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class RiskAssessment:
    assessment_id: str
    status: str
    risk_level: str
    approval: str
    blocking_reason: Optional[str]
    warning_reasons: Tuple[str, ...]
    required_controls: Tuple[str, ...]
    confidence: str
    decision_trace: str
    strategy_decision_id: Optional[str]
    market_state_assessment_id: Optional[str]
    timestamp: str
    version: str
