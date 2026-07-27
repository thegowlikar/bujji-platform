"""Runtime Session Manager runner — composes engine.create_session()
(and the individual transition functions) with optional journaling.
Top-level entry point a future integration point calls; never
re-implements lifecycle logic itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from ..runtime_safety.models import RuntimeAuthorization
from .engine import Clock, _real_clock, create_session
from .models import RuntimeSession, RuntimeSessionPolicy


def run_session_creation(
    authorization: Optional[RuntimeAuthorization],
    session_policy: Optional[RuntimeSessionPolicy],
    existing_session_ids: Tuple[str, ...] = (),
    clock: Clock = _real_clock,
    journal=None,
) -> RuntimeSession:
    """Create one RuntimeSession and optionally journal the result.

    `journal`, if supplied, must expose a `.record(session)` method
    (see bujji/journal/runtime_session_journal.py). This runner never
    authenticates, never connects to a broker -- its only inputs are
    the objects it is handed.
    """
    session = create_session(authorization, session_policy, existing_session_ids, clock=clock)

    if journal is not None:
        journal.record(session)

    return session
