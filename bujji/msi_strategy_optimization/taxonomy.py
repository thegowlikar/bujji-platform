"""bujji.msi_strategy_optimization.taxonomy — Series 108.

Plain string constants only (this project's established convention:
never `enum.Enum`).
"""
from __future__ import annotations

MSI_STRATEGY_OPTIMIZATION_VERSION = "1.0.0"

UNKNOWN = "UNKNOWN"

# ---------------------------------------------------------------------------
# Deliverable 3 -- per-family optimisation objectives. Every family in
# `bujji.msi_strategy_selection_foundation.taxonomy.ALL_STRATEGY_FAMILIES`
# (frozen, imported by identity, never re-listed by value) has at least
# one objective defined below.
# ---------------------------------------------------------------------------
OBJECTIVE_MAXIMIZE_REWARD_RISK = "MAXIMIZE_REWARD_RISK"
OBJECTIVE_MINIMIZE_THETA_DECAY = "MINIMIZE_THETA_DECAY"
OBJECTIVE_MAXIMIZE_THETA = "MAXIMIZE_THETA"
OBJECTIVE_MAXIMIZE_PROBABILITY = "MAXIMIZE_PROBABILITY_OF_PROFIT"
OBJECTIVE_MINIMIZE_GAMMA = "MINIMIZE_GAMMA_EXPOSURE"
OBJECTIVE_BALANCED_WINGS = "MAINTAIN_BALANCED_WINGS"
OBJECTIVE_MAXIMIZE_TERM_STRUCTURE_ADVANTAGE = "MAXIMIZE_TERM_STRUCTURE_ADVANTAGE"
OBJECTIVE_MAXIMIZE_PIN_PROBABILITY = "MAXIMIZE_PIN_PROBABILITY"
OBJECTIVE_CONTROL_TAIL_RISK = "CONTROL_TAIL_RISK"
OBJECTIVE_MAXIMIZE_DIRECTIONAL_EFFICIENCY = "MAXIMIZE_DIRECTIONAL_CAPITAL_EFFICIENCY"
OBJECTIVE_MAXIMIZE_VEGA_EXPOSURE = "MAXIMIZE_VEGA_EXPOSURE"
OBJECTIVE_MINIMIZE_VEGA_EXPOSURE = "MINIMIZE_VEGA_EXPOSURE"
OBJECTIVE_MAXIMIZE_CARRY_EFFICIENCY = "MAXIMIZE_COVERED_CARRY_EFFICIENCY"
OBJECTIVE_MAXIMIZE_SYNTHETIC_EQUIVALENCE = "MAXIMIZE_SYNTHETIC_CAPITAL_EFFICIENCY"

FAMILY_OBJECTIVES = {
    "LONG_DIRECTIONAL": (OBJECTIVE_MAXIMIZE_REWARD_RISK, OBJECTIVE_MAXIMIZE_DIRECTIONAL_EFFICIENCY),
    "SHORT_DIRECTIONAL": (OBJECTIVE_MAXIMIZE_REWARD_RISK, OBJECTIVE_MAXIMIZE_DIRECTIONAL_EFFICIENCY),
    "NEUTRAL_PREMIUM_SELLING": (OBJECTIVE_MAXIMIZE_THETA, OBJECTIVE_MINIMIZE_GAMMA, OBJECTIVE_BALANCED_WINGS),
    "NEUTRAL_PREMIUM_BUYING": (OBJECTIVE_MAXIMIZE_VEGA_EXPOSURE, OBJECTIVE_MAXIMIZE_REWARD_RISK),
    "VOLATILITY_EXPANSION": (OBJECTIVE_MAXIMIZE_VEGA_EXPOSURE, OBJECTIVE_MINIMIZE_THETA_DECAY),
    "VOLATILITY_COMPRESSION": (OBJECTIVE_MINIMIZE_VEGA_EXPOSURE, OBJECTIVE_MAXIMIZE_THETA),
    "IRON_CONDOR": (OBJECTIVE_MAXIMIZE_THETA, OBJECTIVE_MINIMIZE_GAMMA, OBJECTIVE_BALANCED_WINGS),
    "IRON_FLY": (OBJECTIVE_MAXIMIZE_THETA, OBJECTIVE_MAXIMIZE_PIN_PROBABILITY),
    "BUTTERFLY": (OBJECTIVE_MAXIMIZE_PIN_PROBABILITY, OBJECTIVE_MAXIMIZE_REWARD_RISK),
    "CALENDAR": (OBJECTIVE_MAXIMIZE_TERM_STRUCTURE_ADVANTAGE, OBJECTIVE_MINIMIZE_THETA_DECAY),
    "RATIO": (OBJECTIVE_CONTROL_TAIL_RISK, OBJECTIVE_MAXIMIZE_THETA),
    "COVERED": (OBJECTIVE_MAXIMIZE_CARRY_EFFICIENCY, OBJECTIVE_MINIMIZE_THETA_DECAY),
    "SYNTHETIC": (OBJECTIVE_MAXIMIZE_SYNTHETIC_EQUIVALENCE, OBJECTIVE_MAXIMIZE_REWARD_RISK),
}

# ---------------------------------------------------------------------------
# Deliverable 5 -- expiry buckets. DTE thresholds are structural,
# disclosed, never-tuned constants (see config.py), not re-listed here.
# ---------------------------------------------------------------------------
EXPIRY_WEEKLY = "WEEKLY"
EXPIRY_NEXT_WEEKLY = "NEXT_WEEKLY"
EXPIRY_MONTHLY = "MONTHLY"
EXPIRY_FAR_MONTHLY = "FAR_MONTHLY"
ALL_EXPIRY_BUCKETS = (EXPIRY_WEEKLY, EXPIRY_NEXT_WEEKLY, EXPIRY_MONTHLY, EXPIRY_FAR_MONTHLY)

# ---------------------------------------------------------------------------
# Deliverable 6 -- rolling intelligence. Five INDEPENDENT decisions,
# never collapsed into one enum -- `models.RollAssessment` carries five
# separate booleans, each with its own reasoning tuple. These string
# constants exist only to label the single, priority-resolved
# `recommended_action` field for readability.
# ---------------------------------------------------------------------------
ROLL_STRIKE = "ROLL_STRIKE"
ROLL_EXPIRY = "ROLL_EXPIRY"
ROLL_WHOLE_STRATEGY = "ROLL_WHOLE_STRATEGY"
HOLD = "HOLD"
EXIT = "EXIT"
ALL_ROLL_ACTIONS = (EXIT, ROLL_WHOLE_STRATEGY, ROLL_EXPIRY, ROLL_STRIKE, HOLD)  # priority order, highest first

# ---------------------------------------------------------------------------
# Deliverable 7 -- adjustment planner actions.
# ---------------------------------------------------------------------------
ADJUSTMENT_NONE = "NO_ADJUSTMENT"
ADJUSTMENT_REDUCE_DELTA = "REDUCE_DELTA"
ADJUSTMENT_INCREASE_DELTA = "INCREASE_DELTA"
ADJUSTMENT_WIDEN_WINGS = "WIDEN_WINGS"
ADJUSTMENT_NARROW_WINGS = "NARROW_WINGS"
ADJUSTMENT_CONVERT_STRATEGY = "CONVERT_STRATEGY"
ADJUSTMENT_CLOSE_PARTIAL = "CLOSE_PARTIALLY"
ADJUSTMENT_CLOSE_COMPLETE = "CLOSE_COMPLETELY"
ALL_ADJUSTMENT_ACTIONS = (
    ADJUSTMENT_NONE, ADJUSTMENT_REDUCE_DELTA, ADJUSTMENT_INCREASE_DELTA,
    ADJUSTMENT_WIDEN_WINGS, ADJUSTMENT_NARROW_WINGS, ADJUSTMENT_CONVERT_STRATEGY,
    ADJUSTMENT_CLOSE_PARTIAL, ADJUSTMENT_CLOSE_COMPLETE,
)

# ---------------------------------------------------------------------------
# Deliverable 8 -- dynamic strategy conversion. Only pairs whose target
# is a REAL, existing `ALL_STRATEGY_FAMILIES` value are representable.
# The user's own worked examples (Iron Condor -> Broken Wing Butterfly,
# Calendar -> Diagonal) name families that do NOT exist in this frozen
# taxonomy -- disclosed as a missing capability in
# docs/STRATEGY_OPTIMIZATION.md Section 10, never fabricated as a new
# family here (that would require modifying frozen taxonomy, out of
# scope). Each rule below is a real, representable pair only.
# ---------------------------------------------------------------------------
CONVERSION_RULES = (
    {
        "from_family": "IRON_CONDOR", "to_family": "BUTTERFLY",
        "condition": "one short strike's delta has breached the 'tested' threshold "
                     "while the thesis's directional_expectation still favours the untested side "
                     "-- collapsing the tested wing into a pinned, asymmetric structure is more "
                     "capital-efficient than continuing to defend two wings.",
    },
    {
        "from_family": "RATIO", "to_family": "SHORT_DIRECTIONAL",
        "condition": "the position's undefined-risk tail exposure has grown beyond what the "
                     "current lifecycle's portfolio Greeks reading tolerates -- converting to a "
                     "defined-shape directional family caps the open-ended leg.",
    },
    {
        "from_family": "CALENDAR", "to_family": "IRON_FLY",
        "condition": "the front-month/back-month term-structure edge that motivated the Calendar "
                     "has closed (front-month IV has caught up to back-month), but the underlying "
                     "pin thesis (price pinned near the shared strike) still holds -- a defined-risk "
                     "pin structure now dominates a term-structure play with no remaining edge.",
    },
)
