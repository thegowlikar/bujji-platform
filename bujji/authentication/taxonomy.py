"""Authentication & Broker Session Manager vocabulary — BUJJI Options
OS v3, Engineering Series 49, Sprint 1 (v1).

Lives at `bujji/authentication/`, outside the Trading Brain and every
other runtime module built so far -- this module owns broker identity
and broker-session state only. It never places an order, retries, or
reconciles.

Two SEPARATE finite state machines belong to this module --
`authentication_state` and `session_state` -- deliberately kept apart
(a session can be `AUTHENTICATED` but not yet `CONNECTED`/`READY`).
One more finite vocabulary, `failure_reason`, is shared by both.
"""
from __future__ import annotations

AUTHENTICATION_MANAGER_VERSION = "1.0.0"

RECOGNIZED_POLICY_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# Authentication State -- finite, closed. Owned entirely by this
# module; never a broker-native token status, never re-derived from
# production's own `FyersBroker`/`FyersTokenManager` states.
# ---------------------------------------------------------------------------
AUTH_STATE_UNAUTHENTICATED = "UNAUTHENTICATED"
AUTH_STATE_AUTHENTICATING = "AUTHENTICATING"
AUTH_STATE_AUTHENTICATED = "AUTHENTICATED"
AUTH_STATE_EXPIRED = "EXPIRED"
AUTH_STATE_FAILED = "FAILED"

ALL_AUTHENTICATION_STATES = (
    AUTH_STATE_UNAUTHENTICATED,
    AUTH_STATE_AUTHENTICATING,
    AUTH_STATE_AUTHENTICATED,
    AUTH_STATE_EXPIRED,
    AUTH_STATE_FAILED,
)

AUTHENTICATION_STATE_DESCRIPTIONS = {
    AUTH_STATE_UNAUTHENTICATED: "No authentication attempt has been made yet.",
    AUTH_STATE_AUTHENTICATING: "An authentication attempt is in flight, delegated to an injected AuthenticationProviderInterface.",
    AUTH_STATE_AUTHENTICATED: "The injected provider reported success; broker_identity and expires_at are populated.",
    AUTH_STATE_EXPIRED: "The previously-authenticated session's expires_at has passed, per this module's own deterministic clock check.",
    AUTH_STATE_FAILED: "The injected provider reported failure, or prerequisite validation failed before authentication was ever attempted.",
}

# ---------------------------------------------------------------------------
# Session State -- finite, closed. Deliberately separate from
# authentication_state: a session can be AUTHENTICATED but not yet
# CONNECTED or READY.
# ---------------------------------------------------------------------------
SESSION_STATE_DISCONNECTED = "DISCONNECTED"
SESSION_STATE_CONNECTED = "CONNECTED"
SESSION_STATE_READY = "READY"
SESSION_STATE_EXPIRED = "EXPIRED"
SESSION_STATE_FAILED = "FAILED"

ALL_BROKER_SESSION_STATES = (
    SESSION_STATE_DISCONNECTED,
    SESSION_STATE_CONNECTED,
    SESSION_STATE_READY,
    SESSION_STATE_EXPIRED,
    SESSION_STATE_FAILED,
)

BROKER_SESSION_STATE_DESCRIPTIONS = {
    SESSION_STATE_DISCONNECTED: "No broker session has been established yet.",
    SESSION_STATE_CONNECTED: "Authentication succeeded and the session has been marked connected -- bookkeeping only, no network call is implied.",
    SESSION_STATE_READY: "The broker session is ready for a future operational component (order dispatch, etc.) to use.",
    SESSION_STATE_EXPIRED: "Mirrors authentication_state == EXPIRED.",
    SESSION_STATE_FAILED: "Mirrors authentication_state == FAILED.",
}

TERMINAL_AUTH_STATES = (AUTH_STATE_EXPIRED, AUTH_STATE_FAILED)
TERMINAL_SESSION_STATES = (SESSION_STATE_EXPIRED, SESSION_STATE_FAILED)

# ---------------------------------------------------------------------------
# Failure Reason -- finite, closed. Never free text.
# ---------------------------------------------------------------------------
FAILURE_REASON_INVALID_RUNTIME_SESSION = "INVALID_RUNTIME_SESSION"
FAILURE_REASON_INVALID_POLICY = "INVALID_POLICY"
FAILURE_REASON_AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
FAILURE_REASON_SESSION_EXPIRED = "SESSION_EXPIRED"
FAILURE_REASON_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

ALL_FAILURE_REASONS = (
    FAILURE_REASON_INVALID_RUNTIME_SESSION,
    FAILURE_REASON_INVALID_POLICY,
    FAILURE_REASON_AUTHENTICATION_FAILED,
    FAILURE_REASON_SESSION_EXPIRED,
    FAILURE_REASON_INSUFFICIENT_DATA,
)

FAILURE_REASON_DESCRIPTIONS = {
    FAILURE_REASON_INVALID_RUNTIME_SESSION: "The RuntimeSession is missing, or its own session_state is not READY or ACTIVE.",
    FAILURE_REASON_INVALID_POLICY: "AuthenticationPolicy.policy_version is not a recognized value.",
    FAILURE_REASON_AUTHENTICATION_FAILED: "The injected AuthenticationProviderInterface reported an unsuccessful authentication outcome.",
    FAILURE_REASON_SESSION_EXPIRED: "The authenticated session's expires_at has passed.",
    FAILURE_REASON_INSUFFICIENT_DATA: "The RuntimeSession or AuthenticationPolicy was entirely missing.",
}
