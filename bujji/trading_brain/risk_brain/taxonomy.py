"""Risk Brain vocabulary — BUJJI Options OS v3, Engineering Series 35,
Sprint 1.

Five finite vocabularies belong to this module: `status`, `risk_level`,
`approval`, `required_controls`, and `blocking_reason`/`warning_reason`
(two separate closed lists). Nothing here is a dynamic string, a
number, or a probability. Two internal-only ordinal orderings (never
exposed as public vocabulary) exist purely so engine.py can compare and
step confidence/risk -- the same disclosed, calculation-only-ordinal
pattern used since MIC v2's Context Stability sprint and reused in the
Market State Builder (Series 33) and Strategy Selector (Series 34).
"""
from __future__ import annotations

RISK_BRAIN_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Status -- the overall verdict.
# ---------------------------------------------------------------------------
STATUS_VERSION = "1.0.0"

STATUS_UNKNOWN = "UNKNOWN"
STATUS_APPROVED = "APPROVED"
STATUS_APPROVED_WITH_WARNINGS = "APPROVED_WITH_WARNINGS"
STATUS_REJECTED = "REJECTED"
STATUS_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

ALL_STATUSES = (
    STATUS_UNKNOWN,
    STATUS_APPROVED,
    STATUS_APPROVED_WITH_WARNINGS,
    STATUS_REJECTED,
    STATUS_INSUFFICIENT_EVIDENCE,
)

STATUS_DESCRIPTIONS = {
    STATUS_UNKNOWN: "Status could not be determined.",
    STATUS_APPROVED: "The selected strategy is acceptable under today's risk policy with no reservations.",
    STATUS_APPROVED_WITH_WARNINGS: "The selected strategy is acceptable, but only under one or more named controls.",
    STATUS_REJECTED: "The selected strategy is not acceptable under today's risk policy.",
    STATUS_INSUFFICIENT_EVIDENCE: "There is not enough evidence to make a risk determination at all.",
}

# ---------------------------------------------------------------------------
# Risk Level -- how dangerous today's environment is judged to be.
# ---------------------------------------------------------------------------
RISK_LEVEL_VERSION = "1.0.0"

RISK_LEVEL_UNKNOWN = "UNKNOWN"
RISK_LEVEL_LOW = "LOW"
RISK_LEVEL_MODERATE = "MODERATE"
RISK_LEVEL_HIGH = "HIGH"
RISK_LEVEL_EXTREME = "EXTREME"

ALL_RISK_LEVELS = (
    RISK_LEVEL_UNKNOWN,
    RISK_LEVEL_LOW,
    RISK_LEVEL_MODERATE,
    RISK_LEVEL_HIGH,
    RISK_LEVEL_EXTREME,
)

RISK_LEVEL_DESCRIPTIONS = {
    RISK_LEVEL_UNKNOWN: "Risk level could not be assessed.",
    RISK_LEVEL_LOW: "Risk factors are below their normal range.",
    RISK_LEVEL_MODERATE: "Risk factors are within their normal range.",
    RISK_LEVEL_HIGH: "Risk factors are elevated.",
    RISK_LEVEL_EXTREME: "Risk factors are severely elevated.",
}

# ---------------------------------------------------------------------------
# Approval -- the actionable instruction to whatever consumes this
# assessment next (a future Capital Brain / Execution Brain). This
# sprint never acts on it -- it only names it.
# ---------------------------------------------------------------------------
APPROVAL_VERSION = "1.0.0"

APPROVAL_UNKNOWN = "UNKNOWN"
APPROVAL_ALLOW = "ALLOW"
APPROVAL_ALLOW_WITH_CONTROLS = "ALLOW_WITH_CONTROLS"
APPROVAL_DENY = "DENY"

ALL_APPROVALS = (
    APPROVAL_UNKNOWN,
    APPROVAL_ALLOW,
    APPROVAL_ALLOW_WITH_CONTROLS,
    APPROVAL_DENY,
)

APPROVAL_DESCRIPTIONS = {
    APPROVAL_UNKNOWN: "No approval determination could be made.",
    APPROVAL_ALLOW: "The strategy may proceed with no additional controls.",
    APPROVAL_ALLOW_WITH_CONTROLS: "The strategy may proceed only under the named required controls.",
    APPROVAL_DENY: "The strategy may not proceed today.",
}

# ---------------------------------------------------------------------------
# Required Controls -- recommendations only. Never executed by this
# module or any module in this sprint.
# ---------------------------------------------------------------------------
REQUIRED_CONTROL_VERSION = "1.0.0"

CONTROL_UNKNOWN = "UNKNOWN"
CONTROL_REDUCE_SIZE = "REDUCE_SIZE"
CONTROL_REQUIRE_TIGHTER_STOP = "REQUIRE_TIGHTER_STOP"
CONTROL_MONITOR_MORE_FREQUENTLY = "MONITOR_MORE_FREQUENTLY"

ALL_REQUIRED_CONTROLS = (
    CONTROL_UNKNOWN,
    CONTROL_REDUCE_SIZE,
    CONTROL_REQUIRE_TIGHTER_STOP,
    CONTROL_MONITOR_MORE_FREQUENTLY,
)

REQUIRED_CONTROL_DESCRIPTIONS = {
    CONTROL_UNKNOWN: "A control is required but could not be identified.",
    CONTROL_REDUCE_SIZE: "A future Capital Brain should reduce position size versus its default.",
    CONTROL_REQUIRE_TIGHTER_STOP: "A future Position Manager should apply a tighter stop than default.",
    CONTROL_MONITOR_MORE_FREQUENTLY: "A future Position Manager should monitor this position more frequently than default.",
}

# ---------------------------------------------------------------------------
# Blocking Reasons -- finite, only ever populated when approval == DENY.
# ---------------------------------------------------------------------------
BLOCKING_REASON_VERSION = "1.0.0"

BLOCKING_REASON_LOW_CONFIDENCE = "LOW_CONFIDENCE"
BLOCKING_REASON_CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
BLOCKING_REASON_UNKNOWN_MARKET = "UNKNOWN_MARKET"
BLOCKING_REASON_NO_STRATEGY = "NO_STRATEGY"
BLOCKING_REASON_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
BLOCKING_REASON_UNSUPPORTED_STRATEGY = "UNSUPPORTED_STRATEGY"

ALL_BLOCKING_REASONS = (
    BLOCKING_REASON_LOW_CONFIDENCE,
    BLOCKING_REASON_CONTRADICTORY_EVIDENCE,
    BLOCKING_REASON_UNKNOWN_MARKET,
    BLOCKING_REASON_NO_STRATEGY,
    BLOCKING_REASON_INSUFFICIENT_DATA,
    BLOCKING_REASON_UNSUPPORTED_STRATEGY,
)

BLOCKING_REASON_DESCRIPTIONS = {
    BLOCKING_REASON_LOW_CONFIDENCE: "Confidence in the market read is too low to allow any trade.",
    BLOCKING_REASON_CONTRADICTORY_EVIDENCE: "The available evidence contradicts itself too severely to trust.",
    BLOCKING_REASON_UNKNOWN_MARKET: "The market's structure itself is undefined; there is nothing to evaluate a strategy against.",
    BLOCKING_REASON_NO_STRATEGY: "The Strategy Selector did not select a strategy.",
    BLOCKING_REASON_INSUFFICIENT_DATA: "One or both upstream inputs were missing.",
    BLOCKING_REASON_UNSUPPORTED_STRATEGY: "The upstream StrategyDecision reported a selection without naming a strategy -- a defensive, structurally rare check.",
}

# ---------------------------------------------------------------------------
# Warning Reasons -- finite, only ever populated when approval ==
# ALLOW_WITH_CONTROLS.
# ---------------------------------------------------------------------------
WARNING_REASON_VERSION = "1.0.0"

WARNING_REASON_CONTESTED_MARKET = "CONTESTED_MARKET"
WARNING_REASON_LOW_CONFIDENCE = "LOW_CONFIDENCE"
WARNING_REASON_HIGH_VARIABILITY = "HIGH_VARIABILITY"
WARNING_REASON_WEAK_EVIDENCE = "WEAK_EVIDENCE"

ALL_WARNING_REASONS = (
    WARNING_REASON_CONTESTED_MARKET,
    WARNING_REASON_LOW_CONFIDENCE,
    WARNING_REASON_HIGH_VARIABILITY,
    WARNING_REASON_WEAK_EVIDENCE,
)

WARNING_REASON_DESCRIPTIONS = {
    WARNING_REASON_CONTESTED_MARKET: "Exactly one signal contradicted the market read.",
    WARNING_REASON_LOW_CONFIDENCE: "Confidence in the market read is below the strongest tier.",
    WARNING_REASON_HIGH_VARIABILITY: "The market's current regime is unstable.",
    WARNING_REASON_WEAK_EVIDENCE: "No signal actively supported the market read, even though none contradicted it either.",
}

# ---------------------------------------------------------------------------
# Internal-only ordinal orderings. Never exposed as a public vocabulary
# of this module's own.
# ---------------------------------------------------------------------------
CONFIDENCE_ORDER = ("UNKNOWN", "VERY_LOW", "LOW", "MODERATE", "HIGH", "VERY_HIGH")
RISK_LEVEL_ORDER = (RISK_LEVEL_UNKNOWN, RISK_LEVEL_LOW, RISK_LEVEL_MODERATE, RISK_LEVEL_HIGH, RISK_LEVEL_EXTREME)
