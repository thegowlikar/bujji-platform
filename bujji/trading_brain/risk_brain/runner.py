"""Risk Brain runner — composes engine.assess() with optional
journaling. Top-level entry point future Trading Brain modules call;
never re-implements risk evaluation logic itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..market_state.models import MarketStateAssessment
from ..strategy_selector.models import StrategyDecision
from .engine import Clock, _real_clock, assess
from .models import RiskAssessment


def run_risk_assessment(
    strategy_decision: Optional[StrategyDecision],
    market_assessment: Optional[MarketStateAssessment],
    clock: Clock = _real_clock,
    journal=None,
) -> RiskAssessment:
    """Run one risk evaluation cycle and optionally journal the result.

    `journal`, if supplied, must expose a `.record(assessment)` method
    (see bujji/journal/risk_brain_journal.py). This runner never reads
    MIC v2, EvidenceInterpretation, or any earlier upstream layer
    directly -- its only inputs are the StrategyDecision and
    MarketStateAssessment it is handed.
    """
    assessment = assess(strategy_decision, market_assessment, clock=clock)

    if journal is not None:
        journal.record(assessment)

    return assessment
