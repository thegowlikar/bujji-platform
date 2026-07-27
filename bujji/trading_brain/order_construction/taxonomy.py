"""Order Construction Service vocabulary — BUJJI Options OS v3,
Engineering Series 44, Sprint 1 (v1).

Five finite vocabularies belong to this module: `construction_status`,
`execution_policy_value`, `product`, `validity`, and `failure_reason`.
Nothing here is a broker payload, an authentication token, or a retry
parameter. This is the last purely business-logic module in the
Trading Brain pipeline -- everything after it is operational
infrastructure.
"""
from __future__ import annotations

ORDER_CONSTRUCTION_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Construction Status -- overall verdict for one construction attempt.
# ---------------------------------------------------------------------------
CONSTRUCTION_STATUS_UNKNOWN = "UNKNOWN"
CONSTRUCTION_STATUS_CONSTRUCTED = "CONSTRUCTED"
CONSTRUCTION_STATUS_FAILED = "FAILED"

ALL_CONSTRUCTION_STATUSES = (
    CONSTRUCTION_STATUS_UNKNOWN,
    CONSTRUCTION_STATUS_CONSTRUCTED,
    CONSTRUCTION_STATUS_FAILED,
)

CONSTRUCTION_STATUS_DESCRIPTIONS = {
    CONSTRUCTION_STATUS_UNKNOWN: "Construction status could not be determined.",
    CONSTRUCTION_STATUS_CONSTRUCTED: "Every contract leg in the PositionPlan was converted into exactly one OrderRequest.",
    CONSTRUCTION_STATUS_FAILED: "Construction is atomic; if any validation check fails, zero OrderRequests are returned.",
}

# ---------------------------------------------------------------------------
# Execution Policy -- finite, closed. No adaptive policies, no smart
# routing, no optimization.
# ---------------------------------------------------------------------------
EXECUTION_POLICY_MARKET = "MARKET"
EXECUTION_POLICY_LIMIT = "LIMIT"
EXECUTION_POLICY_STOP = "STOP"
EXECUTION_POLICY_STOP_LIMIT = "STOP_LIMIT"

ALL_EXECUTION_POLICIES = (
    EXECUTION_POLICY_MARKET,
    EXECUTION_POLICY_LIMIT,
    EXECUTION_POLICY_STOP,
    EXECUTION_POLICY_STOP_LIMIT,
)

# ---------------------------------------------------------------------------
# Product -- finite, closed, configuration-driven, never inferred.
# ---------------------------------------------------------------------------
PRODUCT_MIS = "MIS"
PRODUCT_NRML = "NRML"

ALL_PRODUCTS = (PRODUCT_MIS, PRODUCT_NRML)

# ---------------------------------------------------------------------------
# Validity -- finite, closed. No broker extensions.
# ---------------------------------------------------------------------------
VALIDITY_DAY = "DAY"
VALIDITY_IOC = "IOC"
VALIDITY_FOK = "FOK"

ALL_VALIDITIES = (VALIDITY_DAY, VALIDITY_IOC, VALIDITY_FOK)

# ---------------------------------------------------------------------------
# Failure Reason -- finite, closed. Never free text.
# ---------------------------------------------------------------------------
FAILURE_REASON_EMPTY_POSITION_PLAN = "EMPTY_POSITION_PLAN"
FAILURE_REASON_INVALID_EXECUTION_POLICY = "INVALID_EXECUTION_POLICY"
FAILURE_REASON_INVALID_PRODUCT = "INVALID_PRODUCT"
FAILURE_REASON_INVALID_VALIDITY = "INVALID_VALIDITY"
FAILURE_REASON_ZERO_QUANTITY = "ZERO_QUANTITY"
FAILURE_REASON_DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
FAILURE_REASON_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

ALL_FAILURE_REASONS = (
    FAILURE_REASON_EMPTY_POSITION_PLAN,
    FAILURE_REASON_INVALID_EXECUTION_POLICY,
    FAILURE_REASON_INVALID_PRODUCT,
    FAILURE_REASON_INVALID_VALIDITY,
    FAILURE_REASON_ZERO_QUANTITY,
    FAILURE_REASON_DUPLICATE_REQUEST,
    FAILURE_REASON_INSUFFICIENT_DATA,
)

FAILURE_REASON_DESCRIPTIONS = {
    FAILURE_REASON_EMPTY_POSITION_PLAN: "The PositionPlan carries no contracts, or its own sizing validation did not PASS.",
    FAILURE_REASON_INVALID_EXECUTION_POLICY: "ExecutionPolicy.policy is not one of MARKET, LIMIT, STOP, STOP_LIMIT.",
    FAILURE_REASON_INVALID_PRODUCT: "TradingConfiguration.product is not one of MIS, NRML.",
    FAILURE_REASON_INVALID_VALIDITY: "TradingConfiguration.validity is not one of DAY, IOC, FOK.",
    FAILURE_REASON_ZERO_QUANTITY: "The PositionPlan's quantity_per_leg is not a positive integer.",
    FAILURE_REASON_DUPLICATE_REQUEST: "Two generated client_order_ids collided -- a defensive check; this should never occur given distinct contract ids.",
    FAILURE_REASON_INSUFFICIENT_DATA: "The PositionPlan, ExecutionPolicy, or TradingConfiguration was entirely missing.",
}
