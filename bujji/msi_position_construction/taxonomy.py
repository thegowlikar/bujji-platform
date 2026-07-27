"""Position Construction Intelligence taxonomy — Series 95. Plain
string constants (house convention, never enum.Enum). Expiry-rule and
risk-profile vocabulary is imported and reused directly from
`bujji.msi_trade_construction.taxonomy` (Deliverable 1 reuse finding),
never re-declared here."""
from __future__ import annotations

from bujji.msi_trade_construction import taxonomy as tc_taxonomy

MSI_POSITION_CONSTRUCTION_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# --- Deliverable 3: construction types -- CONCRETE SHAPE within a family,
# never a strike/expiry value. -------------------------------------------
CONSTRUCTION_SINGLE_LEG = "SINGLE_LEG"
CONSTRUCTION_VERTICAL_DEBIT_SPREAD = "VERTICAL_DEBIT_SPREAD"
CONSTRUCTION_VERTICAL_CREDIT_SPREAD = "VERTICAL_CREDIT_SPREAD"
CONSTRUCTION_SHORT_STRANGLE = "SHORT_STRANGLE"
CONSTRUCTION_LONG_STRANGLE = "LONG_STRANGLE"
CONSTRUCTION_LONG_STRADDLE = "LONG_STRADDLE"
CONSTRUCTION_SHORT_STRADDLE = "SHORT_STRADDLE"
CONSTRUCTION_IRON_CONDOR_SHAPE = "IRON_CONDOR_SHAPE"
CONSTRUCTION_IRON_FLY_SHAPE = "IRON_FLY_SHAPE"
CONSTRUCTION_BUTTERFLY_SHAPE = "BUTTERFLY_SHAPE"
CONSTRUCTION_RATIO_SHAPE = "RATIO_SHAPE"
CONSTRUCTION_COVERED_SHAPE = "COVERED_SHAPE"
CONSTRUCTION_SYNTHETIC_SHAPE = "SYNTHETIC_SHAPE"
CONSTRUCTION_CALENDAR_SHAPE = "CALENDAR_SHAPE"
CONSTRUCTION_NONE = "NONE"

ALL_CONSTRUCTION_TYPES = (
    CONSTRUCTION_SINGLE_LEG, CONSTRUCTION_VERTICAL_DEBIT_SPREAD, CONSTRUCTION_VERTICAL_CREDIT_SPREAD,
    CONSTRUCTION_SHORT_STRANGLE, CONSTRUCTION_LONG_STRANGLE, CONSTRUCTION_LONG_STRADDLE,
    CONSTRUCTION_SHORT_STRADDLE, CONSTRUCTION_IRON_CONDOR_SHAPE, CONSTRUCTION_IRON_FLY_SHAPE,
    CONSTRUCTION_BUTTERFLY_SHAPE, CONSTRUCTION_RATIO_SHAPE, CONSTRUCTION_COVERED_SHAPE,
    CONSTRUCTION_SYNTHETIC_SHAPE, CONSTRUCTION_CALENDAR_SHAPE, CONSTRUCTION_NONE,
)

# --- Expiry plan -- reused DIRECTLY from Series 90, never re-declared. --
EXPIRY_RULE_NEAREST_WEEKLY = tc_taxonomy.EXPIRY_RULE_NEAREST_WEEKLY
EXPIRY_RULE_CALENDAR_NEAR_FAR = tc_taxonomy.EXPIRY_RULE_CALENDAR_NEAR_FAR
EXPIRY_PLAN_NONE = "NONE"

# --- Wing plan --------------------------------------------------------
WING_PLAN_EXPECTED_MOVE_BASED = "EXPECTED_MOVE_BASED"
WING_PLAN_NOT_APPLICABLE = "NOT_APPLICABLE"

# --- Risk profile -- reused DIRECTLY from Series 90, never re-declared. -
RISK_DEFINED = tc_taxonomy.RISK_DEFINED
RISK_UNDEFINED = tc_taxonomy.RISK_UNDEFINED
RISK_UNKNOWN = tc_taxonomy.RISK_UNKNOWN

# --- Payoff profile (combines Strategy Expression's own LIMITED_/
# UNLIMITED_ PROFIT/LOSS vocabulary -- reused conceptually, not
# re-imported, since this package does not depend on
# msi_strategy_expression's engine, only its real assessment TYPE as an
# input parameter). ------------------------------------------------------
PAYOFF_LIMITED_PROFIT_LIMITED_LOSS = "LIMITED_PROFIT_LIMITED_LOSS"
PAYOFF_UNLIMITED_PROFIT_LIMITED_LOSS = "UNLIMITED_PROFIT_LIMITED_LOSS"
PAYOFF_LIMITED_PROFIT_UNLIMITED_LOSS = "LIMITED_PROFIT_UNLIMITED_LOSS"
PAYOFF_UNKNOWN = "UNKNOWN"

# --- Adjustment readiness (NEW concept -- no prior module declares this) -
ADJUSTMENT_FRIENDLY = "ADJUSTMENT_FRIENDLY"
ADJUSTMENT_LIMITED = "ADJUSTMENT_LIMITED"
ADJUSTMENT_NOT_APPLICABLE = "NOT_APPLICABLE"

# --- Qualitative (sign-only, never a numeric magnitude -- Deliverable 1
# disclosed limitation: no real chain/premium data exists at this layer) -
SIGN_POSITIVE = "POSITIVE"
SIGN_NEGATIVE = "NEGATIVE"
SIGN_NEUTRAL = "NEUTRAL"
SIGN_UNKNOWN = "UNKNOWN"

ALL_SIGNS = (SIGN_POSITIVE, SIGN_NEGATIVE, SIGN_NEUTRAL, SIGN_UNKNOWN)

# --- Conviction vocabulary (same as Trade Thesis, reused via pass-through
# comparison, not re-imported as a dependency) ----------------------------
_CONVICTION_RANK = {"NONE": 0, "LOW": 1, "MODERATE": 2, "HIGH": 3}


def conviction_rank(level: str) -> int:
    return _CONVICTION_RANK.get(level, 0)
