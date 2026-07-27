"""Strategy Selector runner — composes engine.select() with optional
journaling. Top-level entry point future Trading Brain modules call;
never re-implements selection logic itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..market_state.models import MarketStateAssessment
from .engine import Clock, _real_clock, select
from .models import StrategyDecision


def run_selection(
    assessment: Optional[MarketStateAssessment],
    clock: Clock = _real_clock,
    journal=None,
) -> StrategyDecision:
    """Run one selection cycle and optionally journal the result.

    `journal`, if supplied, must expose a `.record(decision)` method
    (see bujji/journal/strategy_selector_journal.py). This runner never
    reads MIC v2, EvidenceInterpretation, or any upstream layer
    directly -- its only input is the MarketStateAssessment it is
    handed (or None).
    """
    decision = select(assessment, clock=clock)

    if journal is not None:
        journal.record(decision)

    return decision
