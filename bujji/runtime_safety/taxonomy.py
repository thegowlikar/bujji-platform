"""Runtime Safety Gate vocabulary — BUJJI Options OS v3, Engineering
Series 47, Sprint 1.

Lives at `bujji/runtime_safety/`, outside the Trading Brain and
outside the Runtime Execution Orchestrator -- this is the final,
operationally-isolated checkpoint before any broker interaction.
Nothing here authenticates, refreshes a token, connects to a broker,
retries, polls, or reconciles.

Four finite vocabularies belong to this module: `authorization_state`,
`decision`, `safety_check`, and `failure_reason`.
"""
from __future__ import annotations

RUNTIME_SAFETY_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Recognized versions -- finite, closed sets this gate checks incoming
# objects against. Adding a new recognized version is a deliberate,
# reviewed change to this tuple, never an inferred one.
# ---------------------------------------------------------------------------
RECOGNIZED_CONFIG_VERSIONS = ("1.0.0",)
RECOGNIZED_RUNTIME_POLICY_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# Authorization State -- the overall verdict.
# ---------------------------------------------------------------------------
STATE_AUTHORIZED = "AUTHORIZED"
STATE_AUTHORIZED_WITH_WARNINGS = "AUTHORIZED_WITH_WARNINGS"
STATE_DENIED = "DENIED"
STATE_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

ALL_AUTHORIZATION_STATES = (
    STATE_AUTHORIZED,
    STATE_AUTHORIZED_WITH_WARNINGS,
    STATE_DENIED,
    STATE_INSUFFICIENT_DATA,
)

AUTHORIZATION_STATE_DESCRIPTIONS = {
    STATE_AUTHORIZED: "Every safety check passed; the session is fully authorized to advance.",
    STATE_AUTHORIZED_WITH_WARNINGS: "Every critical safety check passed, but the session has not yet reached DISPATCHED -- authorized, with that noted.",
    STATE_DENIED: "At least one safety check failed; the session must not advance.",
    STATE_INSUFFICIENT_DATA: "One of the three required inputs (ExecutionSession, QualificationPolicy, RuntimeSafetyPolicy) was entirely missing; no check could be run at all.",
}

# ---------------------------------------------------------------------------
# Decision -- a coarser, three-way summary of authorization_state.
# ---------------------------------------------------------------------------
DECISION_ALLOW = "ALLOW"
DECISION_DENY = "DENY"
DECISION_UNKNOWN = "UNKNOWN"

ALL_DECISIONS = (DECISION_ALLOW, DECISION_DENY, DECISION_UNKNOWN)

DECISION_BY_STATE = {
    STATE_AUTHORIZED: DECISION_ALLOW,
    STATE_AUTHORIZED_WITH_WARNINGS: DECISION_ALLOW,
    STATE_DENIED: DECISION_DENY,
    STATE_INSUFFICIENT_DATA: DECISION_UNKNOWN,
}

# ---------------------------------------------------------------------------
# Safety Check -- finite, named checks. Every authorization attempt
# with all three inputs present runs all nine, unconditionally --
# never short-circuiting after the first failure, so both
# `passed_checks` and `failed_checks` are always complete.
# ---------------------------------------------------------------------------
CHECK_EXECUTION_SESSION_VALID = "EXECUTION_SESSION_VALID"
CHECK_QUALIFICATION_FINGERPRINT_PRESENT = "QUALIFICATION_FINGERPRINT_PRESENT"
CHECK_PIPELINE_COMPLETED_SUCCESSFULLY = "PIPELINE_COMPLETED_SUCCESSFULLY"
CHECK_DISPATCH_PLAN_NOT_EMPTY = "DISPATCH_PLAN_NOT_EMPTY"
CHECK_CLIENT_ORDER_IDS_UNIQUE = "CLIENT_ORDER_IDS_UNIQUE"
CHECK_ORDER_COUNT_CONSISTENT = "ORDER_COUNT_CONSISTENT"
CHECK_REPLAY_QUALIFICATION_PASSED = "REPLAY_QUALIFICATION_PASSED"
CHECK_CONFIGURATION_VERSION_RECOGNIZED = "CONFIGURATION_VERSION_RECOGNIZED"
CHECK_RUNTIME_POLICY_RECOGNIZED = "RUNTIME_POLICY_RECOGNIZED"

ALL_SAFETY_CHECKS = (
    CHECK_EXECUTION_SESSION_VALID,
    CHECK_QUALIFICATION_FINGERPRINT_PRESENT,
    CHECK_PIPELINE_COMPLETED_SUCCESSFULLY,
    CHECK_DISPATCH_PLAN_NOT_EMPTY,
    CHECK_CLIENT_ORDER_IDS_UNIQUE,
    CHECK_ORDER_COUNT_CONSISTENT,
    CHECK_REPLAY_QUALIFICATION_PASSED,
    CHECK_CONFIGURATION_VERSION_RECOGNIZED,
    CHECK_RUNTIME_POLICY_RECOGNIZED,
)

# ---------------------------------------------------------------------------
# Failure Reason -- finite, closed. Never free text. Each safety check
# maps to exactly one of these when it fails.
# ---------------------------------------------------------------------------
FAILURE_REASON_INVALID_SESSION = "INVALID_SESSION"
FAILURE_REASON_MISSING_FINGERPRINT = "MISSING_FINGERPRINT"
FAILURE_REASON_FAILED_REPLAY = "FAILED_REPLAY"
FAILURE_REASON_INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
FAILURE_REASON_EMPTY_DISPATCH_PLAN = "EMPTY_DISPATCH_PLAN"
FAILURE_REASON_DUPLICATE_CLIENT_ORDER_ID = "DUPLICATE_CLIENT_ORDER_ID"
FAILURE_REASON_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

ALL_FAILURE_REASONS = (
    FAILURE_REASON_INVALID_SESSION,
    FAILURE_REASON_MISSING_FINGERPRINT,
    FAILURE_REASON_FAILED_REPLAY,
    FAILURE_REASON_INVALID_CONFIGURATION,
    FAILURE_REASON_EMPTY_DISPATCH_PLAN,
    FAILURE_REASON_DUPLICATE_CLIENT_ORDER_ID,
    FAILURE_REASON_INSUFFICIENT_DATA,
)

FAILURE_REASON_DESCRIPTIONS = {
    FAILURE_REASON_INVALID_SESSION: "The ExecutionSession itself is not in a valid, ready-to-advance state, or its dispatch plan and order requests are inconsistent in count.",
    FAILURE_REASON_MISSING_FINGERPRINT: "QualificationPolicy carries no qualification_fingerprint.",
    FAILURE_REASON_FAILED_REPLAY: "QualificationPolicy reports the replay did not PASS, was not chain-valid, or was not deterministic.",
    FAILURE_REASON_INVALID_CONFIGURATION: "The ExecutionSession's own version, or RuntimeSafetyPolicy's own policy_version, is not a recognized value.",
    FAILURE_REASON_EMPTY_DISPATCH_PLAN: "The ExecutionSession's dispatch plan has zero instructions.",
    FAILURE_REASON_DUPLICATE_CLIENT_ORDER_ID: "Two or more OrderRequests in the session share the same client_order_id.",
    FAILURE_REASON_INSUFFICIENT_DATA: "One of ExecutionSession, QualificationPolicy, or RuntimeSafetyPolicy was entirely missing.",
}

# Map from a failing safety check name to the failure reason it
# produces -- each check maps to exactly one finite reason.
FAILURE_REASON_BY_CHECK = {
    CHECK_EXECUTION_SESSION_VALID: FAILURE_REASON_INVALID_SESSION,
    CHECK_QUALIFICATION_FINGERPRINT_PRESENT: FAILURE_REASON_MISSING_FINGERPRINT,
    CHECK_PIPELINE_COMPLETED_SUCCESSFULLY: FAILURE_REASON_FAILED_REPLAY,
    CHECK_DISPATCH_PLAN_NOT_EMPTY: FAILURE_REASON_EMPTY_DISPATCH_PLAN,
    CHECK_CLIENT_ORDER_IDS_UNIQUE: FAILURE_REASON_DUPLICATE_CLIENT_ORDER_ID,
    CHECK_ORDER_COUNT_CONSISTENT: FAILURE_REASON_INVALID_SESSION,
    CHECK_REPLAY_QUALIFICATION_PASSED: FAILURE_REASON_FAILED_REPLAY,
    CHECK_CONFIGURATION_VERSION_RECOGNIZED: FAILURE_REASON_INVALID_CONFIGURATION,
    CHECK_RUNTIME_POLICY_RECOGNIZED: FAILURE_REASON_INVALID_CONFIGURATION,
}

WARNING_SESSION_NOT_YET_DISPATCHED = "SESSION_NOT_YET_DISPATCHED"
