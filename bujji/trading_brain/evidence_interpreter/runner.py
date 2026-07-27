"""Evidence Interpreter runner — composes engine.interpret() with
optional journaling. This is the top-level entry point future Trading
Brain modules call; it never re-implements translation logic itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from .engine import Clock, _real_clock, interpret
from .models import EvidenceInterpretation


def run_interpretation(
    market_context: Optional[str] = None,
    market_opinion: Optional[str] = None,
    context_stability: Optional[str] = None,
    calibration: Optional[str] = None,
    governance: Optional[str] = None,
    lifecycle: Optional[str] = None,
    contract: Optional[str] = None,
    source: str = "paper",
    reasoning_summary: str = "",
    context_id: Optional[str] = None,
    clock: Clock = _real_clock,
    journal=None,
) -> EvidenceInterpretation:
    """Run one translation cycle and optionally journal the result.

    `journal`, if supplied, must expose a `.record(interpretation)`
    method (see bujji/journal/evidence_interpreter_journal.py). This
    runner never constructs a journal itself and never reads MIC v2
    directly -- it only forwards already-extracted classification
    strings to engine.interpret().
    """
    interpretation = interpret(
        market_context=market_context,
        market_opinion=market_opinion,
        context_stability=context_stability,
        calibration=calibration,
        governance=governance,
        lifecycle=lifecycle,
        contract=contract,
        source=source,
        reasoning_summary=reasoning_summary,
        context_id=context_id,
        clock=clock,
    )

    if journal is not None:
        journal.record(interpretation)

    return interpretation
