"""Position Sizing Engine runner — composes engine.size_position() with
optional journaling. Top-level entry point a future component (e.g.
the Runtime Execution Service) calls; never re-implements sizing logic
itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from ..capital_brain.models import CapitalDecision
from ..nifty_contract_builder.models import NiftyOptionContract
from .config import PositionSizingConfig
from .engine import Clock, _real_clock, size_position
from .models import CapitalPolicy, LotSpecification, PositionPlan


def run_sizing(
    capital_decision: Optional[CapitalDecision],
    contracts: Optional[Tuple[NiftyOptionContract, ...]],
    capital_policy: Optional[CapitalPolicy],
    lot_spec: Optional[LotSpecification],
    sizing_config: PositionSizingConfig,
    clock: Clock = _real_clock,
    journal=None,
) -> PositionPlan:
    """Run one sizing cycle and optionally journal the result.

    `journal`, if supplied, must expose a `.record(plan)` method (see
    bujji/journal/position_sizing_journal.py). This runner never
    connects to a broker, never authenticates -- its only inputs are
    the objects it is handed.
    """
    plan = size_position(capital_decision, contracts, capital_policy, lot_spec, sizing_config, clock=clock)

    if journal is not None:
        journal.record(plan)

    return plan
