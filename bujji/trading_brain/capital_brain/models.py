"""Capital Brain models — frozen, immutable capital authorization.

Nothing here calculates a lot, a quantity, a margin figure, or a
broker exposure number. `CapitalDecision` authorizes a capital POLICY
-- a committee's budget, not a trading desk's implementation of it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class CapitalDecision:
    decision_id: str
    capital_intent: str
    allocation_status: str
    allocation_reason: str
    allocation_constraints: Tuple[str, ...]
    required_controls: Tuple[str, ...]
    confidence: str
    decision_trace: str
    risk_assessment_id: Optional[str]
    timestamp: str
    version: str
