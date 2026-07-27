"""Runtime Session Manager models — frozen, immutable records.

Nothing here authenticates, connects to a broker, or submits an
order. `RuntimeSession` owns state transitions only -- every field
describes lifecycle bookkeeping, never a broker-side or
authentication-side outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RuntimeSessionPolicy:
    policy_version: str
    allow_pause_resume: bool = True
    version: str = "1.0.0"


@dataclass(frozen=True)
class RuntimeSession:
    session_id: str
    authorization_id: Optional[str]
    session_state: str
    session_policy: Optional[RuntimeSessionPolicy]
    lifecycle_trace: str
    failure_reason: Optional[str]
    created_at: str
    updated_at: str
    version: str
