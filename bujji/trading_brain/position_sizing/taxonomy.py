"""Position Sizing Engine vocabulary — BUJJI Options OS v3, Engineering
Series 43, Sprint 1 (v1).

`capital_intent` is reused verbatim from the frozen Capital Brain
(Series 36) -- never redefined here. Three new, finite vocabularies
belong to this module: `capital_policy_value`, `validation_status`,
and `failure_reason`. Nothing here is a lot count computed from
emotion, confidence, or prediction -- every lot count comes from
`PositionSizingConfig`, and every quantity is `lots * lot_size`,
nothing else.
"""
from __future__ import annotations

from ..capital_brain.taxonomy import ALL_CAPITAL_INTENTS

POSITION_SIZING_VERSION = "1.0.0"

UNDERLYING_NIFTY = "NIFTY"

# ---------------------------------------------------------------------------
# Capital Intent -- reused verbatim from the frozen Capital Brain.
# ---------------------------------------------------------------------------
REUSED_CAPITAL_INTENTS = ALL_CAPITAL_INTENTS

# ---------------------------------------------------------------------------
# Capital Policy -- mirrors the naming already used in production's
# own `RiskConfig.capital_policy` (discovered during the Series 41
# production audit: STRICT | ESTIMATED | SIMULATION | CERTIFIED). This
# module does not import that production config -- it replicates only
# the naming, for the same reason the Broker Adapter (Series 40) and
# NIFTY Contract Builder (Series 42) replicated production naming
# rather than importing production code.
# ---------------------------------------------------------------------------
CAPITAL_POLICY_VERSION = "1.0.0"

CAPITAL_POLICY_STRICT = "STRICT"
CAPITAL_POLICY_ESTIMATED = "ESTIMATED"
CAPITAL_POLICY_SIMULATION = "SIMULATION"
CAPITAL_POLICY_CERTIFIED = "CERTIFIED"

ALL_CAPITAL_POLICIES = (
    CAPITAL_POLICY_STRICT,
    CAPITAL_POLICY_ESTIMATED,
    CAPITAL_POLICY_SIMULATION,
    CAPITAL_POLICY_CERTIFIED,
)

CAPITAL_POLICY_DESCRIPTIONS = {
    CAPITAL_POLICY_STRICT: "Capital figures are live-certified; sizing may proceed under the configured lot table.",
    CAPITAL_POLICY_ESTIMATED: "Capital figures are estimated, not live-certified; sizing still proceeds under the same configured lot table -- this module applies no different math per policy value, it only validates the value is recognized.",
    CAPITAL_POLICY_SIMULATION: "Capital figures are simulated (e.g. paper mode); same lot table applies.",
    CAPITAL_POLICY_CERTIFIED: "Capital figures are formally certified; same lot table applies.",
}

# ---------------------------------------------------------------------------
# Validation Status -- overall verdict.
# ---------------------------------------------------------------------------
VALIDATION_STATUS_UNKNOWN = "UNKNOWN"
VALIDATION_STATUS_PASSED = "PASSED"
VALIDATION_STATUS_FAILED = "FAILED"

ALL_VALIDATION_STATUSES = (
    VALIDATION_STATUS_UNKNOWN,
    VALIDATION_STATUS_PASSED,
    VALIDATION_STATUS_FAILED,
)

# ---------------------------------------------------------------------------
# Failure Reason -- finite, closed. Never free text.
# ---------------------------------------------------------------------------
FAILURE_REASON_UNKNOWN_CAPITAL_INTENT = "UNKNOWN_CAPITAL_INTENT"
FAILURE_REASON_INVALID_LOT_SPECIFICATION = "INVALID_LOT_SPECIFICATION"
FAILURE_REASON_EMPTY_CONTRACT_SET = "EMPTY_CONTRACT_SET"
FAILURE_REASON_INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
FAILURE_REASON_ZERO_QUANTITY = "ZERO_QUANTITY"
FAILURE_REASON_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

ALL_FAILURE_REASONS = (
    FAILURE_REASON_UNKNOWN_CAPITAL_INTENT,
    FAILURE_REASON_INVALID_LOT_SPECIFICATION,
    FAILURE_REASON_EMPTY_CONTRACT_SET,
    FAILURE_REASON_INVALID_CONFIGURATION,
    FAILURE_REASON_ZERO_QUANTITY,
    FAILURE_REASON_INSUFFICIENT_DATA,
)

FAILURE_REASON_DESCRIPTIONS = {
    FAILURE_REASON_UNKNOWN_CAPITAL_INTENT: "The CapitalDecision names a capital_intent this module does not recognize.",
    FAILURE_REASON_INVALID_LOT_SPECIFICATION: "The LotSpecification is missing, names a non-NIFTY underlying, or has a non-positive lot size.",
    FAILURE_REASON_EMPTY_CONTRACT_SET: "No contracts were supplied to size.",
    FAILURE_REASON_INVALID_CONFIGURATION: "PositionSizingConfig itself is invalid (a non-positive lot count, or full_lots exceeding max_lots), or CapitalPolicy names an unrecognized value.",
    FAILURE_REASON_ZERO_QUANTITY: "The capital intent maps to zero lots (e.g. NONE) or the computed quantity is otherwise zero -- an honest 'no position' outcome, never fabricated as a real size.",
    FAILURE_REASON_INSUFFICIENT_DATA: "A required input (CapitalDecision, contracts, CapitalPolicy, or LotSpecification) was entirely missing, or the supplied contract set contains duplicate legs that make sizing ambiguous.",
}
