"""NIFTY Contract Builder vocabulary — BUJJI Options OS v3, Engineering
Series 42, Sprint 1 (v1, NIFTY only).

Five finite vocabularies belong to this module: `construction_status`,
`failure_reason`, `option_type`, `side`, and `moneyness` (the internal
strike-selection primitive). Nothing here is a broker payload, a lot
size, a margin figure, a Greek, or a probability.
"""
from __future__ import annotations

NIFTY_CONTRACT_BUILDER_VERSION = "1.0.0"

UNDERLYING_NIFTY = "NIFTY"
STRIKE_INTERVAL = 50

# ---------------------------------------------------------------------------
# Construction Status -- overall verdict for one construction attempt.
# ---------------------------------------------------------------------------
CONSTRUCTION_STATUS_VERSION = "1.0.0"

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
    CONSTRUCTION_STATUS_CONSTRUCTED: "Every leg of the strategy's template was successfully matched to a real chain entry.",
    CONSTRUCTION_STATUS_FAILED: "At least one leg could not be constructed; construction is atomic, so zero contracts are returned.",
}

# ---------------------------------------------------------------------------
# Failure Reason -- finite, closed. Never free text.
# ---------------------------------------------------------------------------
FAILURE_REASON_VERSION = "1.0.0"

FAILURE_REASON_MISSING_OPTION_CHAIN = "MISSING_OPTION_CHAIN"
FAILURE_REASON_NO_WEEKLY_EXPIRY = "NO_WEEKLY_EXPIRY"
FAILURE_REASON_NO_MATCHING_STRIKE = "NO_MATCHING_STRIKE"
FAILURE_REASON_UNKNOWN_STRATEGY = "UNKNOWN_STRATEGY"
FAILURE_REASON_INVALID_SPOT = "INVALID_SPOT"
FAILURE_REASON_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
# Engineering Series 63: a strategy the Strategy Selector's own
# registry recognizes (it is a real, named, registered strategy -- not
# a typo or a corrupted value), but for which v1 deliberately ships no
# contract template (see UNSUPPORTED_REGISTERED_STRATEGIES below and
# docs/NIFTY_CONTRACT_BUILDER_ARCHITECTURE.md). Distinct from
# FAILURE_REASON_UNKNOWN_STRATEGY, which now means "not registered by
# the Strategy Selector at all" -- a genuine anomaly, never expected in
# normal operation. Historical Qualification Campaign v2 (2026-07-09,
# COVERED_CALL) surfaced this ambiguity: both cases previously produced
# the same failure reason and were indistinguishable in the campaign
# report, even though one is a documented scope boundary and the other
# would be a real defect.
FAILURE_REASON_STRATEGY_OUT_OF_V1_SCOPE = "STRATEGY_OUT_OF_V1_SCOPE"

ALL_FAILURE_REASONS = (
    FAILURE_REASON_MISSING_OPTION_CHAIN,
    FAILURE_REASON_NO_WEEKLY_EXPIRY,
    FAILURE_REASON_NO_MATCHING_STRIKE,
    FAILURE_REASON_UNKNOWN_STRATEGY,
    FAILURE_REASON_INVALID_SPOT,
    FAILURE_REASON_INSUFFICIENT_DATA,
    FAILURE_REASON_STRATEGY_OUT_OF_V1_SCOPE,
)

FAILURE_REASON_DESCRIPTIONS = {
    FAILURE_REASON_MISSING_OPTION_CHAIN: "No NIFTY option chain snapshot was supplied.",
    FAILURE_REASON_NO_WEEKLY_EXPIRY: "The supplied chain names no weekly expiry (or, for a two-expiry template, fewer than two).",
    FAILURE_REASON_NO_MATCHING_STRIKE: "A computed (strike, option_type, expiry) triple has no matching entry in the supplied chain.",
    FAILURE_REASON_UNKNOWN_STRATEGY: "The selected strategy is not registered by the Strategy Selector at all -- a genuine anomaly, never expected in normal operation.",
    FAILURE_REASON_INVALID_SPOT: "No usable NIFTY spot price was supplied.",
    FAILURE_REASON_INSUFFICIENT_DATA: "The StrategyDecision or CapitalDecision itself was missing or named no strategy.",
    FAILURE_REASON_STRATEGY_OUT_OF_V1_SCOPE: "This strategy is registered by the Strategy Selector but has no v1 contract template -- a disclosed, deliberate scope limit, not an oversight. See docs/NIFTY_CONTRACT_BUILDER_ARCHITECTURE.md.",
}

# ---------------------------------------------------------------------------
# Option Type / Side -- finite.
# ---------------------------------------------------------------------------
OPTION_TYPE_CE = "CE"
OPTION_TYPE_PE = "PE"
ALL_OPTION_TYPES = (OPTION_TYPE_CE, OPTION_TYPE_PE)

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
ALL_SIDES = (SIDE_BUY, SIDE_SELL)

# ---------------------------------------------------------------------------
# Moneyness -- the internal strike-selection primitive used by
# taxonomy-level templates. `ATM_PLUS_1`/`ATM_MINUS_1` are declared for
# taxonomy completeness (the specification's own strike-selection
# policy names them) but are not used by any v1 template below --
# every v1 template expresses its strikes via `ATM`/`OTM1`/`OTM2`
# (moneyness relative to option type), which is sufficient for all six
# templates this sprint implements. See
# docs/NIFTY_CONTRACT_BUILDER_ARCHITECTURE.md.
# ---------------------------------------------------------------------------
MONEYNESS_ATM = "ATM"
MONEYNESS_ATM_PLUS_1 = "ATM_PLUS_1"
MONEYNESS_ATM_MINUS_1 = "ATM_MINUS_1"
MONEYNESS_OTM1 = "OTM1"
MONEYNESS_OTM2 = "OTM2"

ALL_MONEYNESS = (
    MONEYNESS_ATM,
    MONEYNESS_ATM_PLUS_1,
    MONEYNESS_ATM_MINUS_1,
    MONEYNESS_OTM1,
    MONEYNESS_OTM2,
)

UNREACHABLE_MONEYNESS_THIS_SPRINT = (MONEYNESS_ATM_PLUS_1, MONEYNESS_ATM_MINUS_1)

# ---------------------------------------------------------------------------
# Supported Strategies -- v1 template registry keys. Any
# StrategyDecision naming a strategy outside this tuple resolves to
# UNKNOWN_STRATEGY. Declared here so tests and docs share one source
# of truth with engine.py::TEMPLATES.
# ---------------------------------------------------------------------------
SUPPORTED_STRATEGIES = (
    "PREMIUM_VWAP_STRADDLE",
    "IRON_FLY",
    "IRON_CONDOR",
    "DIRECTIONAL_CALL_SPREAD",
    "DIRECTIONAL_PUT_SPREAD",
    "CALENDAR_SPREAD",
)

# The remaining five strategies registered in the Strategy Selector's
# own registry (Series 34) -- LONG_STRADDLE, LONG_STRANGLE,
# SHORT_STRANGLE, COVERED_CALL, CASH_SECURED_PUT -- have no v1
# contract template and resolve to UNKNOWN_STRATEGY. This is a
# disclosed, deliberate v1 scope limit, not an oversight -- see
# docs/NIFTY_CONTRACT_BUILDER_ARCHITECTURE.md.
UNSUPPORTED_REGISTERED_STRATEGIES = (
    "LONG_STRADDLE",
    "LONG_STRANGLE",
    "SHORT_STRANGLE",
    "COVERED_CALL",
    "CASH_SECURED_PUT",
)
