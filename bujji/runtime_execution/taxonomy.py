"""Runtime Execution Orchestrator vocabulary — BUJJI Options OS v3,
Engineering Series 45, Sprint 1 (v1).

Lives at `bujji/runtime_execution/`, outside the Trading Brain --
this is operational orchestration, not business logic, mirroring the
Broker Adapter's own placement (Series 40) per the Series 41
architecture review's own separation principle.

`order_type` values are reused verbatim from the frozen Order
Construction Service (Series 44) -- never redefined here. Two new,
finite vocabularies belong to this module: `execution_state` and
`failure_reason`.
"""
from __future__ import annotations

from ..trading_brain.order_construction.taxonomy import ALL_EXECUTION_POLICIES

RUNTIME_EXECUTION_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Order Type -- reused verbatim from the frozen Order Construction
# Service's own ExecutionPolicy vocabulary.
# ---------------------------------------------------------------------------
REUSED_ORDER_TYPES = ALL_EXECUTION_POLICIES

# ---------------------------------------------------------------------------
# Execution State -- finite state machine. No broker states (FILLED,
# REJECTED, CANCELLED, ...) belong here -- those live entirely inside
# production's own `Broker`/`ExecutionEngine`/`OrderResult`, which this
# module never redefines or shadows.
# ---------------------------------------------------------------------------
STATE_CREATED = "CREATED"
STATE_VALIDATED = "VALIDATED"
STATE_READY = "READY"
STATE_DISPATCH_PENDING = "DISPATCH_PENDING"
STATE_DISPATCHED = "DISPATCHED"
STATE_FAILED_VALIDATION = "FAILED_VALIDATION"
STATE_ABORTED = "ABORTED"

ALL_EXECUTION_STATES = (
    STATE_CREATED,
    STATE_VALIDATED,
    STATE_READY,
    STATE_DISPATCH_PENDING,
    STATE_DISPATCHED,
    STATE_FAILED_VALIDATION,
    STATE_ABORTED,
)

EXECUTION_STATE_DESCRIPTIONS = {
    STATE_CREATED: "The conceptual starting point before any validation runs. Never materialized as a return value -- build_session() validates synchronously start to finish, so no function in this module ever returns a session sitting in CREATED.",
    STATE_VALIDATED: "All OrderRequests passed validation. An intermediate conceptual state folded into READY once the dispatch plan is also built -- see docs/RUNTIME_EXECUTION_ORCHESTRATOR_ARCHITECTURE.md.",
    STATE_READY: "Validation passed and a dispatch plan was successfully built -- one instruction per OrderRequest, none fabricated.",
    STATE_DISPATCH_PENDING: "The session has been queued for hand-off to an injected ExecutionEngineInterface, but no call has been made yet.",
    STATE_DISPATCHED: "Every dispatch instruction was handed to the injected ExecutionEngineInterface without it raising.",
    STATE_FAILED_VALIDATION: "At least one validation check failed; construction is atomic, so zero dispatch instructions exist.",
    STATE_ABORTED: "Either no OrderRequests were supplied at all, or the injected ExecutionEngineInterface raised during dispatch.",
}

# `VALIDATED` is declared in the finite state machine per the
# specification's own list, but this sprint's `build_session()` folds
# it into `READY` in the same synchronous call (validation and
# dispatch-plan construction happen back-to-back, never as two
# separately observable steps) -- so `VALIDATED` alone, like
# `CREATED`, is never independently returned by any function in this
# module. Both are kept in the taxonomy for completeness and for a
# future series that might split validation and planning into two
# separately awaitable steps.
UNREACHABLE_STATES_THIS_SPRINT = (STATE_CREATED, STATE_VALIDATED)

# ---------------------------------------------------------------------------
# Failure Reason -- finite, closed. Never free text.
# ---------------------------------------------------------------------------
FAILURE_REASON_EMPTY_SESSION = "EMPTY_SESSION"
FAILURE_REASON_INVALID_ORDER_REQUEST = "INVALID_ORDER_REQUEST"
FAILURE_REASON_DUPLICATE_CLIENT_ORDER_ID = "DUPLICATE_CLIENT_ORDER_ID"
FAILURE_REASON_INVALID_DISPATCH_PLAN = "INVALID_DISPATCH_PLAN"
FAILURE_REASON_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

ALL_FAILURE_REASONS = (
    FAILURE_REASON_EMPTY_SESSION,
    FAILURE_REASON_INVALID_ORDER_REQUEST,
    FAILURE_REASON_DUPLICATE_CLIENT_ORDER_ID,
    FAILURE_REASON_INVALID_DISPATCH_PLAN,
    FAILURE_REASON_INSUFFICIENT_DATA,
)

FAILURE_REASON_DESCRIPTIONS = {
    FAILURE_REASON_EMPTY_SESSION: "Zero OrderRequests were supplied (an empty, but non-None, tuple).",
    FAILURE_REASON_INVALID_ORDER_REQUEST: "At least one OrderRequest is missing a contract, has a non-positive quantity, has an unrecognized order_type, or has an empty client_order_id.",
    FAILURE_REASON_DUPLICATE_CLIENT_ORDER_ID: "Two or more OrderRequests in the same session share the same client_order_id.",
    FAILURE_REASON_INVALID_DISPATCH_PLAN: "A defensive check: the constructed dispatch plan does not have exactly one instruction per validated OrderRequest.",
    FAILURE_REASON_INSUFFICIENT_DATA: "No OrderRequests were supplied at all (None, not an empty tuple).",
}

# ---------------------------------------------------------------------------
# Validation Result -- overall verdict, separate from execution_state.
# ---------------------------------------------------------------------------
VALIDATION_RESULT_UNKNOWN = "UNKNOWN"
VALIDATION_RESULT_PASSED = "PASSED"
VALIDATION_RESULT_FAILED = "FAILED"

ALL_VALIDATION_RESULTS = (
    VALIDATION_RESULT_UNKNOWN,
    VALIDATION_RESULT_PASSED,
    VALIDATION_RESULT_FAILED,
)
