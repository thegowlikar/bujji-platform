"""bujji.msi_dynamic_management.taxonomy — Series 109.

Plain string constants (this project's established convention).
"""
from __future__ import annotations

MSI_DYNAMIC_MANAGEMENT_VERSION = "1.0.0"
UNKNOWN = "UNKNOWN"

# ---------------------------------------------------------------------------
# Deliverable 3 -- the six INDEPENDENT decision types. Each produces its
# own assessment (models.py) -- never combined into one object.
# ---------------------------------------------------------------------------
DECISION_STRIKE_ROLL = "STRIKE_ROLL"
DECISION_EXPIRY_ROLL = "EXPIRY_ROLL"
DECISION_DELTA_REBALANCE = "DELTA_REBALANCE"
DECISION_WING_ADJUSTMENT = "WING_ADJUSTMENT"
DECISION_STRATEGY_CONVERSION = "STRATEGY_CONVERSION"
DECISION_FULL_EXIT = "FULL_EXIT"
ALL_DECISION_TYPES = (
    DECISION_STRIKE_ROLL, DECISION_EXPIRY_ROLL, DECISION_DELTA_REBALANCE,
    DECISION_WING_ADJUSTMENT, DECISION_STRATEGY_CONVERSION, DECISION_FULL_EXIT,
)

# ---------------------------------------------------------------------------
# Deliverable 4 -- priority. Each assessment's priority is COMPUTED from
# its own real evidence (see engine.py's `_classify_priority`) -- this
# is a labelled SCALE, not a fixed lookup table of decision-type ->
# priority. The same decision type can land at any of these four levels
# on different real days, depending on the evidence.
# ---------------------------------------------------------------------------
PRIORITY_MANDATORY = "MANDATORY"
PRIORITY_RECOMMENDED = "RECOMMENDED"
PRIORITY_OPTIONAL = "OPTIONAL"
PRIORITY_AVOID = "AVOID"
ALL_PRIORITIES = (PRIORITY_MANDATORY, PRIORITY_RECOMMENDED, PRIORITY_OPTIONAL, PRIORITY_AVOID)
PRIORITY_RANK = {PRIORITY_MANDATORY: 3, PRIORITY_RECOMMENDED: 2, PRIORITY_OPTIONAL: 1, PRIORITY_AVOID: 0}

# ---------------------------------------------------------------------------
# Deliverable 5 -- Strategy Transition Engine. Only pairs whose `to_family`
# is a REAL value in `msi_strategy_selection_foundation.taxonomy
# .ALL_STRATEGY_FAMILIES` are representable. The user's own worked
# examples name category labels ("Short Premium", "Defined Risk", "Long
# Premium") that are not themselves families -- each is mapped here to
# ONE concrete, representative real family pair, disclosed explicitly in
# docs/DYNAMIC_MANAGEMENT.md Section 5 (not a full category-to-category
# mapping, which this frozen taxonomy's granularity cannot represent).
# `msi_strategy_optimization.taxonomy.CONVERSION_RULES` (Series 108,
# frozen) is reused BY IDENTITY as the base set -- never re-declared.
# ---------------------------------------------------------------------------
TRANSITION_RULES = (
    {
        "from_family": "LONG_DIRECTIONAL", "to_family": "CALENDAR",
        "condition": "Vertical -> Calendar: directional realization has stalled (theta is now decaying the "
                     "position faster than the underlying is moving toward thesis) while the day's real "
                     "volatility term structure offers a carry advantage -- converting captures time-value "
                     "edge the vertical spread cannot.",
    },
    {
        "from_family": "NEUTRAL_PREMIUM_SELLING", "to_family": "IRON_CONDOR",
        "condition": "Short Premium -> Defined Risk (one representative pair; NEUTRAL_PREMIUM_SELLING is this "
                     "codebase's undefined-risk premium-selling family): tail-risk tolerance has tightened "
                     "(portfolio Greeks approaching a watch threshold) -- converting to the defined-risk "
                     "structural equivalent caps the open-ended leg while preserving the same premium-selling intent.",
    },
    {
        "from_family": "NEUTRAL_PREMIUM_BUYING", "to_family": "RATIO",
        "condition": "Long Premium -> Ratio (one representative pair): a long-premium position's directional "
                     "thesis has stalled without invalidating -- converting to a ratio structure reduces net "
                     "cost basis while retaining convexity, at the cost of introducing undefined-risk tail exposure.",
    },
)
