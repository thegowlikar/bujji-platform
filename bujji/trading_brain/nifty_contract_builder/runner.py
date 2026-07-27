"""NIFTY Contract Builder runner — composes engine.build_contracts()
with optional journaling. Top-level entry point a future component
(e.g. the Runtime Execution Service) calls; never re-implements
construction logic itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..capital_brain.models import CapitalDecision
from ..strategy_selector.models import StrategyDecision
from .engine import Clock, _real_clock, build_contracts
from .models import ContractConstructionResult, NiftyOptionChainSnapshot, NiftySpotSnapshot


def run_construction(
    strategy_decision: Optional[StrategyDecision],
    capital_decision: Optional[CapitalDecision],
    spot_snapshot: Optional[NiftySpotSnapshot],
    option_chain: Optional[NiftyOptionChainSnapshot],
    clock: Clock = _real_clock,
    journal=None,
) -> ContractConstructionResult:
    """Run one construction cycle and optionally journal the result.

    `journal`, if supplied, must expose a `.record(result)` method
    (see bujji/journal/nifty_contract_builder_journal.py). This runner
    never connects to a broker, never fetches live data itself -- its
    only inputs are the objects it is handed.
    """
    result = build_contracts(strategy_decision, capital_decision, spot_snapshot, option_chain, clock=clock)

    if journal is not None:
        journal.record(result)

    return result
