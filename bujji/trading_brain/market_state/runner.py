"""Market State Builder runner — composes engine.assess() with optional
journaling. Top-level entry point future Trading Brain modules call;
never re-implements fusion logic itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..evidence_interpreter.models import EvidenceInterpretation
from .engine import Clock, _real_clock, assess
from .models import MarketStateAssessment


def run_assessment(
    interpretation: EvidenceInterpretation,
    clock: Clock = _real_clock,
    journal=None,
) -> MarketStateAssessment:
    """Run one fusion cycle and optionally journal the result.

    `journal`, if supplied, must expose a `.record(assessment)` method
    (see bujji/journal/market_state_journal.py). This runner never
    reads MIC v2 or any upstream layer directly -- its only input is
    the EvidenceInterpretation it is handed.
    """
    assessment = assess(interpretation, clock=clock)

    if journal is not None:
        journal.record(assessment)

    return assessment
