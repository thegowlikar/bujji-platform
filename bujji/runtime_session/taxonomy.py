"""Runtime Session Manager vocabulary — BUJJI Options OS v3,
Engineering Series 48, Sprint 1 (v1).

Lives at `bujji/runtime_session/`, outside the Trading Brain, the
Runtime Execution Orchestrator, the Broker Adapter, and the Runtime
Safety Gate -- this module owns state transitions only. Nothing here
authenticates, connects to a broker, submits an order, retries, polls,
or reconciles.

Two finite vocabularies belong to this module: `session_state` and
`failure_reason`.
"""
from __future__ import annotations

RUNTIME_SESSION_VERSION = "1.0.0"

RECOGNIZED_SESSION_POLICY_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# Session State -- finite state machine. No broker-specific states, no
# authentication states, no order states -- those belong entirely to
# later Engineering Series and their own modules, never here.
# ---------------------------------------------------------------------------
STATE_CREATED = "CREATED"
STATE_INITIALIZED = "INITIALIZED"
STATE_READY = "READY"
STATE_ACTIVE = "ACTIVE"
STATE_PAUSED = "PAUSED"
STATE_COMPLETED = "COMPLETED"
STATE_ABORTED = "ABORTED"
STATE_FAILED = "FAILED"

ALL_SESSION_STATES = (
    STATE_CREATED,
    STATE_INITIALIZED,
    STATE_READY,
    STATE_ACTIVE,
    STATE_PAUSED,
    STATE_COMPLETED,
    STATE_ABORTED,
    STATE_FAILED,
)

TERMINAL_STATES = (STATE_COMPLETED, STATE_ABORTED, STATE_FAILED)

SESSION_STATE_DESCRIPTIONS = {
    STATE_CREATED: "The session object exists and passed creation validation. No lifecycle step has run yet.",
    STATE_INITIALIZED: "The session has been initialized -- a deterministic, explicit transition from CREATED.",
    STATE_READY: "The session is ready to become active -- a deterministic, explicit transition from INITIALIZED.",
    STATE_ACTIVE: "The session is live within this lifecycle manager's own bookkeeping. No broker, order, or authentication state is implied.",
    STATE_PAUSED: "The session has been explicitly paused from ACTIVE, and may be explicitly resumed back to ACTIVE.",
    STATE_COMPLETED: "The session reached a normal, successful end from ACTIVE. Terminal.",
    STATE_ABORTED: "The session was explicitly aborted from any non-terminal state. Terminal.",
    STATE_FAILED: "Creation validation itself failed; the session never left CREATED in a valid form. Terminal.",
}

# Explicit adjacency -- every transition this module allows. No
# implicit jumps, no hidden state: any transition attempt not listed
# here is refused (a no-op that returns the session unchanged).
ALLOWED_TRANSITIONS = {
    STATE_CREATED: (STATE_INITIALIZED, STATE_ABORTED),
    STATE_INITIALIZED: (STATE_READY, STATE_ABORTED),
    STATE_READY: (STATE_ACTIVE, STATE_ABORTED),
    STATE_ACTIVE: (STATE_PAUSED, STATE_COMPLETED, STATE_ABORTED),
    STATE_PAUSED: (STATE_ACTIVE, STATE_ABORTED),
    STATE_COMPLETED: (),
    STATE_ABORTED: (),
    STATE_FAILED: (),
}

# ---------------------------------------------------------------------------
# Failure Reason -- finite, closed. Never free text.
# ---------------------------------------------------------------------------
FAILURE_REASON_INVALID_AUTHORIZATION = "INVALID_AUTHORIZATION"
FAILURE_REASON_INVALID_POLICY = "INVALID_POLICY"
FAILURE_REASON_MISSING_FINGERPRINT = "MISSING_FINGERPRINT"
FAILURE_REASON_DUPLICATE_SESSION = "DUPLICATE_SESSION"
FAILURE_REASON_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

ALL_FAILURE_REASONS = (
    FAILURE_REASON_INVALID_AUTHORIZATION,
    FAILURE_REASON_INVALID_POLICY,
    FAILURE_REASON_MISSING_FINGERPRINT,
    FAILURE_REASON_DUPLICATE_SESSION,
    FAILURE_REASON_INSUFFICIENT_DATA,
)

FAILURE_REASON_DESCRIPTIONS = {
    FAILURE_REASON_INVALID_AUTHORIZATION: "The RuntimeAuthorization's own decision is not ALLOW (i.e. it was DENIED or itself INSUFFICIENT_DATA).",
    FAILURE_REASON_INVALID_POLICY: "RuntimeSessionPolicy.policy_version is not a recognized value.",
    FAILURE_REASON_MISSING_FINGERPRINT: "The RuntimeAuthorization itself never confirmed QUALIFICATION_FINGERPRINT_PRESENT among its own passed checks.",
    FAILURE_REASON_DUPLICATE_SESSION: "The deterministically-derived session_id already exists among the caller-supplied existing_session_ids.",
    FAILURE_REASON_INSUFFICIENT_DATA: "The RuntimeAuthorization or RuntimeSessionPolicy was entirely missing.",
}
