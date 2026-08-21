"""Trade Construction Foundation taxonomy — Series 90.

Plain string constants, never enum.Enum (house convention since Series
78). Every value here is a CATEGORY label, never a numeric threshold --
thresholds live in config.py, per the same separation MSI has used
throughout.
"""
from __future__ import annotations

MSI_TRADE_CONSTRUCTION_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# --- Expiry selection rules --------------------------------------------
EXPIRY_RULE_NEAREST_WEEKLY = "NEAREST_WEEKLY"
EXPIRY_RULE_NEAREST_MONTHLY = "NEAREST_MONTHLY"
EXPIRY_RULE_CALENDAR_NEAR_FAR = "CALENDAR_NEAR_FAR"  # CALENDAR family only: two expiries.

ALL_EXPIRY_RULES = (EXPIRY_RULE_NEAREST_WEEKLY, EXPIRY_RULE_NEAREST_MONTHLY, EXPIRY_RULE_CALENDAR_NEAR_FAR)

# --- Expiry rejection reasons -------------------------------------------
EXPIRY_REJECTED_BELOW_MIN_DTE = "BELOW_MIN_DTE"
EXPIRY_REJECTED_ABOVE_MAX_DTE = "ABOVE_MAX_DTE"
EXPIRY_REJECTED_NOT_MONTHLY = "NOT_MONTHLY_CANDIDATE"
EXPIRY_REJECTED_NO_FAR_LEG = "NO_FAR_EXPIRY_AVAILABLE"

# --- Risk profile --------------------------------------------------------
RISK_DEFINED = "DEFINED_RISK"
RISK_UNDEFINED = "UNDEFINED_RISK"
RISK_UNKNOWN = "UNKNOWN"

ALL_RISK_PROFILES = (RISK_DEFINED, RISK_UNDEFINED, RISK_UNKNOWN)

# --- Strike leg roles ------------------------------------------------------
ROLE_SHORT = "SHORT"
ROLE_LONG = "LONG"
ROLE_WING_LOWER = "WING_LOWER"
ROLE_WING_UPPER = "WING_UPPER"
ROLE_BODY = "BODY"
ROLE_COVERED_SHORT_CALL = "COVERED_SHORT_CALL"
ROLE_NEAR_EXPIRY_SHORT = "NEAR_EXPIRY_SHORT"
ROLE_FAR_EXPIRY_LONG = "FAR_EXPIRY_LONG"
ROLE_SYNTHETIC_LONG_CALL = "SYNTHETIC_LONG_CALL"
ROLE_SYNTHETIC_SHORT_PUT = "SYNTHETIC_SHORT_PUT"

ALL_STRIKE_ROLES = (
    ROLE_SHORT, ROLE_LONG, ROLE_WING_LOWER, ROLE_WING_UPPER, ROLE_BODY,
    ROLE_COVERED_SHORT_CALL, ROLE_NEAR_EXPIRY_SHORT, ROLE_FAR_EXPIRY_LONG,
    ROLE_SYNTHETIC_LONG_CALL, ROLE_SYNTHETIC_SHORT_PUT,
)

# --- Construction rejection reasons (Deliverable 5 -- fail closed) -------
REJECT_NO_SUITABLE_EXPIRY = "NO_SUITABLE_EXPIRY"
REJECT_STRIKE_UNAVAILABLE = "STRIKE_UNAVAILABLE"
REJECT_LIQUIDITY_INSUFFICIENT = "LIQUIDITY_INSUFFICIENT"
REJECT_IMPOSSIBLE_WING_WIDTH = "IMPOSSIBLE_WING_WIDTH"
REJECT_INCONSISTENT_CHAIN = "INCONSISTENT_CHAIN"
REJECT_IV_UNSOLVABLE = "IV_UNSOLVABLE"
REJECT_UNSUPPORTED_FAMILY = "UNSUPPORTED_FAMILY"

ALL_REJECTION_REASONS = (
    REJECT_NO_SUITABLE_EXPIRY, REJECT_STRIKE_UNAVAILABLE, REJECT_LIQUIDITY_INSUFFICIENT,
    REJECT_IMPOSSIBLE_WING_WIDTH, REJECT_INCONSISTENT_CHAIN, REJECT_IV_UNSOLVABLE,
    REJECT_UNSUPPORTED_FAMILY,
)

# Families this package knows how to construct (Deliverable 3). A family
# absent here (none, currently -- all 13 SSF families are covered) would
# fail closed with REJECT_UNSUPPORTED_FAMILY rather than guess a shape.
SUPPORTED_FAMILIES = (
    "LONG_DIRECTIONAL", "SHORT_DIRECTIONAL",
    "NEUTRAL_PREMIUM_SELLING", "NEUTRAL_PREMIUM_BUYING",
    "VOLATILITY_EXPANSION", "VOLATILITY_COMPRESSION",
    "CALENDAR", "RATIO", "BUTTERFLY", "IRON_CONDOR", "IRON_FLY",
    "COVERED", "SYNTHETIC",
    # Added 2026-08-19 (operator directive: three-part regime selection).
    # A directional VOLATILITY SELLER needs a credit spread that leans with
    # the trend; the codebase had none -- SHORT_DIRECTIONAL is a naked
    # single short, not a spread, so a trending market had no sellable
    # defined-risk shape at all and always resolved to no-trade.
    "BULL_PUT_SPREAD", "BEAR_CALL_SPREAD",
)

# Families whose risk profile is structurally defined-risk (every leg has
# an offsetting long leg / bounded max loss by construction, e.g. max
# loss = debit paid, or long+short legs cap the spread) vs undefined-risk
# (a naked short leg, or a synthetic/ratio-unbalanced position with
# unbounded loss on one side) -- a STRUCTURAL fact about the shape,
# never derived from historical drawdown or P&L.
DEFINED_RISK_FAMILIES = (
    "IRON_CONDOR", "IRON_FLY", "BUTTERFLY", "CALENDAR",
    "LONG_DIRECTIONAL", "NEUTRAL_PREMIUM_BUYING", "VOLATILITY_EXPANSION",
    # Both credit spreads pair every short leg with a protective long leg,
    # so max loss is bounded by (wing width - credit) BY CONSTRUCTION --
    # the same structural fact that puts IRON_CONDOR here, not a claim
    # derived from any historical drawdown.
    "BULL_PUT_SPREAD", "BEAR_CALL_SPREAD",
)
UNDEFINED_RISK_FAMILIES = (
    "SHORT_DIRECTIONAL", "NEUTRAL_PREMIUM_SELLING", "VOLATILITY_COMPRESSION",
    "COVERED", "RATIO", "SYNTHETIC",
)
