"""Runtime Safety Gate runner — composes engine.authorize() with
optional journaling. Top-level entry point a future integration point
calls; never re-implements authorization logic itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..runtime_execution.models import ExecutionSession
from .engine import Clock, _real_clock, authorize
from .models import QualificationPolicy, RuntimeAuthorization, RuntimeSafetyPolicy


def run_authorization(
    session: Optional[ExecutionSession],
    qualification_policy: Optional[QualificationPolicy],
    runtime_safety_policy: Optional[RuntimeSafetyPolicy],
    clock: Clock = _real_clock,
    journal=None,
) -> RuntimeAuthorization:
    """Run one authorization cycle and optionally journal the result.

    `journal`, if supplied, must expose a `.record(authorization)`
    method (see bujji/journal/runtime_safety_journal.py). This runner
    never authenticates, never connects to a broker -- its only inputs
    are the objects it is handed.
    """
    authorization = authorize(session, qualification_policy, runtime_safety_policy, clock=clock)

    if journal is not None:
        journal.record(authorization)

    return authorization
