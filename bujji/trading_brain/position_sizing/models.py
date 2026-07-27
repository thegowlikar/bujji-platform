"""Position Sizing Engine models — frozen, immutable records.

Nothing here reads a broker margin figure, models exposure, or applies
leverage. `PositionPlan` names, at most, one uniform lot count and one
resulting quantity, applied identically to every leg of a strategy --
never sized independently, never fabricated when validation fails.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from ..nifty_contract_builder.models import NiftyOptionContract


@dataclass(frozen=True)
class CapitalPolicy:
    policy: str
    version: str = "1.0.0"


@dataclass(frozen=True)
class LotSpecification:
    underlying: str
    lot_size: int
    effective_date: str
    version: str = "1.0.0"


@dataclass(frozen=True)
class PositionPlan:
    plan_id: str
    contracts: Tuple[NiftyOptionContract, ...]
    lots_per_leg: int
    quantity_per_leg: int
    capital_intent: str
    sizing_policy: str
    validation: str
    sizing_reason: str
    sizing_trace: str
    failure_reason: Optional[str]
    capital_decision_id: Optional[str]
    timestamp: str
    version: str
