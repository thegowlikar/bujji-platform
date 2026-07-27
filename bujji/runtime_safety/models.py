"""Runtime Safety Gate models — frozen, immutable records.

Nothing here authenticates, connects to a broker, or executes.
`RuntimeAuthorization` is the final, auditable checkpoint's own
verdict -- it never partially authorizes a session; either every
critical check passed, or the session is denied / reported as
insufficient data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class QualificationPolicy:
    """A summary of a Series 46 replay qualification result, exactly
    as much as this gate needs to check -- never the full report.
    """

    replay_status: str
    qualification_fingerprint: Optional[str]
    chain_valid: bool
    deterministic: bool
    version: str = "1.0.0"


@dataclass(frozen=True)
class RuntimeSafetyPolicy:
    policy_version: str
    allow_authorized_with_warnings: bool = True
    version: str = "1.0.0"


@dataclass(frozen=True)
class RuntimeAuthorization:
    authorization_id: str
    execution_session_id: Optional[str]
    authorization_state: str
    decision: str
    failed_checks: Tuple[str, ...]
    passed_checks: Tuple[str, ...]
    failure_reasons: Tuple[str, ...]
    warnings: Tuple[str, ...]
    authorization_trace: str
    timestamp: str
    version: str
