"""Authentication & Broker Session Manager engine — BUJJI Options OS
v3, Engineering Series 49, Sprint 1 (v1).

The Runtime Session Manager (Series 48) owns the lifecycle of an
execution session. This module owns broker identity and broker-session
state -- it never places an order, dispatches a trade, or reconciles a
fill.

`AuthenticationProviderInterface` is a structural `typing.Protocol` --
an abstract shape (`authenticate() -> AuthenticationOutcome`), never an
import of production's concrete `bujji.broker.fyers_token_manager.FyersTokenManager`
or `bujji.broker.fyers.FyersBroker`. Per the Series 41 architecture
review's own "reuse, never duplicate" principle, this module wraps and
orchestrates whatever production authentication capability is injected
as `provider` -- it never reimplements token refresh, session
validation, or broker-specific auth logic itself. This deterministic
layer contains zero broker SDK imports and zero network calls of its
own; any real I/O happens entirely inside `provider`, injected from
outside this package.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from ..runtime_session.models import RuntimeSession
from . import taxonomy
from .models import AuthenticationOutcome, AuthenticationPolicy, BrokerSession

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


@runtime_checkable
class AuthenticationProviderInterface(Protocol):
    """The minimal shape this module needs from a production
    authentication capability. A wrapper around production's own
    `FyersTokenManager`/`FyersBroker.connect()` (identified during the
    Series 41 audit) would satisfy this shape -- this Protocol is
    never a replacement for it, only a seam this module can be tested
    against without importing it.
    """

    def authenticate(self) -> AuthenticationOutcome: ...


def _broker_session_id(runtime_session: Optional[RuntimeSession], timestamp: str) -> str:
    seed = "|".join([runtime_session.session_id if runtime_session else "NONE", timestamp])
    return "BS-" + hashlib.md5(seed.encode()).hexdigest()[:16]


def _failure(
    reason: str,
    runtime_session: Optional[RuntimeSession],
    timestamp: str,
) -> BrokerSession:
    trace = f"FAILED because {reason}."
    return BrokerSession(
        broker_session_id=_broker_session_id(runtime_session, timestamp),
        runtime_session_id=runtime_session.session_id if runtime_session else None,
        broker_identity=None,
        authentication_state=taxonomy.AUTH_STATE_FAILED,
        session_state=taxonomy.SESSION_STATE_FAILED,
        authentication_trace=trace,
        failure_reason=reason,
        created_at=timestamp,
        expires_at=None,
        updated_at=timestamp,
        version=taxonomy.AUTHENTICATION_MANAGER_VERSION,
    )


def create_broker_session(
    runtime_session: Optional[RuntimeSession],
    policy: Optional[AuthenticationPolicy],
    clock: Clock = _real_clock,
) -> BrokerSession:
    """Validate prerequisites and create a new, UNAUTHENTICATED
    BrokerSession bound to an already-managed RuntimeSession.

    Never fabricates an authenticated session: any validation failure
    yields a terminal FAILED BrokerSession.
    """
    timestamp = clock().isoformat()

    if runtime_session is None or policy is None:
        return _failure(taxonomy.FAILURE_REASON_INSUFFICIENT_DATA, runtime_session, timestamp)

    if runtime_session.session_state not in ("READY", "ACTIVE"):
        return _failure(taxonomy.FAILURE_REASON_INVALID_RUNTIME_SESSION, runtime_session, timestamp)

    if policy.policy_version not in taxonomy.RECOGNIZED_POLICY_VERSIONS:
        return _failure(taxonomy.FAILURE_REASON_INVALID_POLICY, runtime_session, timestamp)

    trace = (
        f"Runtime Session {runtime_session.session_state}. "
        f"{taxonomy.AUTH_STATE_UNAUTHENTICATED}. {taxonomy.SESSION_STATE_DISCONNECTED}."
    )

    return BrokerSession(
        broker_session_id=_broker_session_id(runtime_session, timestamp),
        runtime_session_id=runtime_session.session_id,
        broker_identity=None,
        authentication_state=taxonomy.AUTH_STATE_UNAUTHENTICATED,
        session_state=taxonomy.SESSION_STATE_DISCONNECTED,
        authentication_trace=trace,
        failure_reason=None,
        created_at=timestamp,
        expires_at=None,
        updated_at=timestamp,
        version=taxonomy.AUTHENTICATION_MANAGER_VERSION,
    )


def _updated(broker_session: BrokerSession, timestamp: str, **changes) -> BrokerSession:
    fields = {f: getattr(broker_session, f) for f in broker_session.__dataclass_fields__}
    fields.update(changes)
    fields["updated_at"] = timestamp
    return BrokerSession(**fields)


def begin_authentication(broker_session: BrokerSession, clock: Clock = _real_clock) -> BrokerSession:
    """UNAUTHENTICATED -> AUTHENTICATING. A no-op otherwise."""
    if broker_session.authentication_state != taxonomy.AUTH_STATE_UNAUTHENTICATED:
        return broker_session

    timestamp = clock().isoformat()
    trace = broker_session.authentication_trace + f" {taxonomy.AUTH_STATE_AUTHENTICATING}."
    return _updated(
        broker_session,
        timestamp,
        authentication_state=taxonomy.AUTH_STATE_AUTHENTICATING,
        authentication_trace=trace,
    )


def complete_authentication(
    broker_session: BrokerSession,
    provider: AuthenticationProviderInterface,
    policy: AuthenticationPolicy,
    clock: Clock = _real_clock,
) -> BrokerSession:
    """Call the injected provider once and apply its outcome.

    A no-op if not currently AUTHENTICATING. Contains no I/O of its
    own -- whatever `provider.authenticate()` actually does (wrap a
    real production token manager, or a test stub) is entirely
    outside this module's control and import graph.
    """
    if broker_session.authentication_state != taxonomy.AUTH_STATE_AUTHENTICATING:
        return broker_session

    timestamp = clock().isoformat()
    outcome: AuthenticationOutcome = provider.authenticate()

    if not outcome.success:
        trace = (
            broker_session.authentication_trace
            + f" Provider reported failure. {taxonomy.AUTH_STATE_FAILED}."
        )
        return _updated(
            broker_session,
            timestamp,
            authentication_state=taxonomy.AUTH_STATE_FAILED,
            session_state=taxonomy.SESSION_STATE_FAILED,
            authentication_trace=trace,
            failure_reason=taxonomy.FAILURE_REASON_AUTHENTICATION_FAILED,
        )

    expires_at = outcome.expires_at
    if expires_at is None:
        expires_at = (clock() + timedelta(seconds=policy.session_ttl_seconds)).isoformat()

    trace = broker_session.authentication_trace + f" {taxonomy.AUTH_STATE_AUTHENTICATED}."
    return _updated(
        broker_session,
        timestamp,
        authentication_state=taxonomy.AUTH_STATE_AUTHENTICATED,
        broker_identity=outcome.broker_identity,
        expires_at=expires_at,
        authentication_trace=trace,
        failure_reason=None,
    )


def connect(broker_session: BrokerSession, clock: Clock = _real_clock) -> BrokerSession:
    """DISCONNECTED -> CONNECTED, only once AUTHENTICATED. A no-op
    otherwise."""
    if (
        broker_session.authentication_state != taxonomy.AUTH_STATE_AUTHENTICATED
        or broker_session.session_state != taxonomy.SESSION_STATE_DISCONNECTED
    ):
        return broker_session

    timestamp = clock().isoformat()
    trace = broker_session.authentication_trace + f" {taxonomy.SESSION_STATE_CONNECTED}."
    return _updated(
        broker_session, timestamp, session_state=taxonomy.SESSION_STATE_CONNECTED, authentication_trace=trace
    )


def mark_ready(broker_session: BrokerSession, clock: Clock = _real_clock) -> BrokerSession:
    """CONNECTED -> READY, only once AUTHENTICATED. A no-op
    otherwise."""
    if (
        broker_session.authentication_state != taxonomy.AUTH_STATE_AUTHENTICATED
        or broker_session.session_state != taxonomy.SESSION_STATE_CONNECTED
    ):
        return broker_session

    timestamp = clock().isoformat()
    trace = broker_session.authentication_trace + f" {taxonomy.SESSION_STATE_READY}."
    return _updated(
        broker_session, timestamp, session_state=taxonomy.SESSION_STATE_READY, authentication_trace=trace
    )


def check_expiry(broker_session: BrokerSession, clock: Clock = _real_clock) -> BrokerSession:
    """Deterministic expiry check: if `expires_at` has passed as of
    `clock()`, transition both state machines to EXPIRED. A no-op if
    not currently AUTHENTICATED, or if there is no expires_at to check.
    """
    if broker_session.authentication_state != taxonomy.AUTH_STATE_AUTHENTICATED:
        return broker_session
    if not broker_session.expires_at:
        return broker_session

    now = clock()
    expires = datetime.fromisoformat(broker_session.expires_at)
    if now < expires:
        return broker_session

    timestamp = now.isoformat()
    trace = broker_session.authentication_trace + f" {taxonomy.AUTH_STATE_EXPIRED}."
    return _updated(
        broker_session,
        timestamp,
        authentication_state=taxonomy.AUTH_STATE_EXPIRED,
        session_state=taxonomy.SESSION_STATE_EXPIRED,
        authentication_trace=trace,
        failure_reason=taxonomy.FAILURE_REASON_SESSION_EXPIRED,
    )
