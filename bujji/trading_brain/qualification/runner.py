"""Decision Pipeline Qualification runner — composes
engine.run_qualification() with optional journaling. This is the
top-level entry point for validating the frozen Trading Brain
end-to-end; it never re-implements pipeline or validation logic
itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .engine import Clock, _real_clock, run_qualification
from .models import DecisionPipelineQualification, PipelineInput


def run_pipeline_qualification(
    pipeline_input: PipelineInput,
    clock: Clock = _real_clock,
    replay_count: int = 10,
    journal=None,
) -> DecisionPipelineQualification:
    """Run one qualification cycle and optionally journal the result.

    `journal`, if supplied, must expose a `.record(qualification)`
    method (see
    bujji/journal/decision_pipeline_qualification_journal.py). This
    runner never connects to a broker, execution engine, or order
    manager -- it only calls the frozen Trading Brain's own stage
    engines and validates their combined behavior.
    """
    qualification = run_qualification(pipeline_input, clock=clock, replay_count=replay_count)

    if journal is not None:
        journal.record(qualification)

    return qualification
