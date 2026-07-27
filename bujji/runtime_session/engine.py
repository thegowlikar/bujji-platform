"""Runtime Session Manager engine — BUJJI Options OS v3, Engineering
Series 48, Sprint 1 (v1).

Business decisions are complete (the Trading Brain). Authorization is
complete (the Runtime Safety Gate, Series 47). This module owns state
transitions only -- the lifecycle of an already-authorized execution
session. It never authenticates, connects to a broker, submits an
order, retries, polls, or reconciles.

Inputs to `create_session()` are exactly a `RuntimeAuthorization`
(Series 47) and a `RuntimeSessionPolicy` -- never a broker, an
authentication module, a token manager, a network call,
`ExecutionEngine`, or a broker SDK.

Every transition is deterministic and explicit: `ALLOWED_TRANSITIONS`
is the one and only adjacency table this module consults, and an
attempted transition not listed there is always a no-op (the session
is returned unchanged) -- no implicit jumps, no hidden state.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, Optional, Tuple

from ..runtime_safety.models import RuntimeAuthorization
from . import taxonomy
from .models import RuntimeSession, RuntimeSessionPolicy

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def _session_id(authorization: RuntimeAuthorization, timestamp: str) -> str:
    seed = "|".join([authorization.authorization_id, timestamp])
    return "RS-" + hashlib.md5(seed.encode()).hexdigest()[:16]


def _failure(
    reason: str,
    authorization: Optional[RuntimeAuthorization],
    timestamp: str,
) -> RuntimeSession:
    seed = "|".join(
        [authorization.authorization_id if authorization else "NONE", reason, timestamp]
    )
    session_id = "RS-" + hashlib.md5(seed.encode()).hexdigest()[:16]
    trace = f"FAILED because {reason}."
    return RuntimeSession(
        session_id=session_id,
        authorization_id=authorization.authorization_id if authorization else None,
        session_state=taxonomy.STATE_FAILED,
        session_policy=None,
        lifecycle_trace=trace,
        failure_reason=reason,
        created_at=timestamp,
        updated_at=timestamp,
        version=taxonomy.RUNTIME_SESSION_VERSION,
    )


def create_session(
    authorization: Optional[RuntimeAuthorization],
    session_policy: Optional[RuntimeSessionPolicy],
    existing_session_ids: Tuple[str, ...] = (),
    clock: Clock = _real_clock,
) -> RuntimeSession:
    """Create a new RuntimeSession from an already-authorized
    RuntimeAuthorization, or fail honestly.

    Never fabricates a session: any validation failure yields a
    terminal FAILED session with zero lifecycle capability, not a
    partially-valid one.
    """
    timestamp = clock().isoformat()

    if authorization is None or session_policy is None:
        return _failure(taxonomy.FAILURE_REASON_INSUFFICIENT_DATA, authorization, timestamp)

    if authorization.decision != "ALLOW":
        return _failure(taxonomy.FAILURE_REASON_INVALID_AUTHORIZATION, authorization, timestamp)

    if "QUALIFICATION_FINGERPRINT_PRESENT" not in authorization.passed_checks:
        return _failure(taxonomy.FAILURE_REASON_MISSING_FINGERPRINT, authorization, timestamp)

    if session_policy.policy_version not in taxonomy.RECOGNIZED_SESSION_POLICY_VERSIONS:
        return _failure(taxonomy.FAILURE_REASON_INVALID_POLICY, authorization, timestamp)

    session_id = _session_id(authorization, timestamp)
    if session_id in existing_session_ids:
        return _failure(taxonomy.FAILURE_REASON_DUPLICATE_SESSION, authorization, timestamp)

    trace = (
        f"Runtime Authorized ({authorization.authorization_state}). "
        f"Session Created. {taxonomy.STATE_CREATED}."
    )

    return RuntimeSession(
        session_id=session_id,
        authorization_id=authorization.authorization_id,
        session_state=taxonomy.STATE_CREATED,
        session_policy=session_policy,
        lifecycle_trace=trace,
        failure_reason=None,
        created_at=timestamp,
        updated_at=timestamp,
        version=taxonomy.RUNTIME_SESSION_VERSION,
    )


def _transition(session: RuntimeSession, target_state: str, clock: Clock) -> RuntimeSession:
    """The one place every state transition in this module is applied.

    A no-op (returns the session unchanged) unless `target_state` is
    listed in `ALLOWED_TRANSITIONS` for the session's current state --
    never an implicit jump.
    """
    allowed = taxonomy.ALLOWED_TRANSITIONS.get(session.session_state, ())
    if target_state not in allowed:
        return session

    timestamp = clock().isoformat()
    trace = session.lifecycle_trace + f" {target_state}."
    return RuntimeSession(
        session_id=session.session_id,
        authorization_id=session.authorization_id,
        session_state=target_state,
        session_policy=session.session_policy,
        lifecycle_trace=trace,
        failure_reason=None,
        created_at=session.created_at,
        updated_at=timestamp,
        version=session.version,
    )


def initialize(session: RuntimeSession, clock: Clock = _real_clock) -> RuntimeSession:
    return _transition(session, taxonomy.STATE_INITIALIZED, clock)


def mark_ready(session: RuntimeSession, clock: Clock = _real_clock) -> RuntimeSession:
    return _transition(session, taxonomy.STATE_READY, clock)


def activate(session: RuntimeSession, clock: Clock = _real_clock) -> RuntimeSession:
    return _transition(session, taxonomy.STATE_ACTIVE, clock)


def pause(session: RuntimeSession, clock: Clock = _real_clock) -> RuntimeSession:
    if session.session_policy is not None and not session.session_policy.allow_pause_resume:
        return session
    return _transition(session, taxonomy.STATE_PAUSED, clock)


def resume(session: RuntimeSession, clock: Clock = _real_clock) -> RuntimeSession:
    if session.session_policy is not None and not session.session_policy.allow_pause_resume:
        return session
    return _transition(session, taxonomy.STATE_ACTIVE, clock)


def complete(session: RuntimeSession, clock: Clock = _real_clock) -> RuntimeSession:
    return _transition(session, taxonomy.STATE_COMPLETED, clock)


def abort(session: RuntimeSession, clock: Clock = _real_clock) -> RuntimeSession:
    return _transition(session, taxonomy.STATE_ABORTED, clock)
