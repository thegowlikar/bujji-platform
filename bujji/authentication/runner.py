"""Authentication & Broker Session Manager runner — composes
engine.create_broker_session() (and the individual lifecycle
functions) with optional journaling. Top-level entry point a future
integration point calls; never re-implements authentication logic
itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..runtime_session.models import RuntimeSession
from .engine import Clock, _real_clock, create_broker_session
from .models import AuthenticationPolicy, BrokerSession


def run_broker_session_creation(
    runtime_session: Optional[RuntimeSession],
    policy: Optional[AuthenticationPolicy],
    clock: Clock = _real_clock,
    journal=None,
) -> BrokerSession:
    """Create one BrokerSession and optionally journal the result.

    `journal`, if supplied, must expose a `.record(session)` method
    (see bujji/journal/authentication_journal.py). This runner never
    authenticates itself, never connects to a broker -- its only
    inputs are the objects it is handed.
    """
    session = create_broker_session(runtime_session, policy, clock=clock)

    if journal is not None:
        journal.record(session)

    return session
