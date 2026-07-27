"""Capital Brain runner — composes engine.authorize() with optional
journaling. Top-level entry point future Trading Brain modules call;
never re-implements capital policy logic itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..risk_brain.models import RiskAssessment
from .engine import Clock, _real_clock, authorize
from .models import CapitalDecision


def run_capital_authorization(
    risk_assessment: Optional[RiskAssessment],
    clock: Clock = _real_clock,
    journal=None,
) -> CapitalDecision:
    """Run one capital authorization cycle and optionally journal it.

    `journal`, if supplied, must expose a `.record(decision)` method
    (see bujji/journal/capital_brain_journal.py). This runner never
    reads MIC v2, the Strategy Selector, the Market State Builder, or
    any earlier upstream layer directly -- its only input is the
    RiskAssessment it is handed.
    """
    decision = authorize(risk_assessment, clock=clock)

    if journal is not None:
        journal.record(decision)

    return decision
