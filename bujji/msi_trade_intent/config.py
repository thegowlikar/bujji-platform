"""Static configuration for the Trade Intent Intelligence engine.

Every value below is fixed, disclosed configuration -- never fit or
tuned against outcomes, mirroring `bujji.msi_strategy_eligibility.
config`'s exact posture. Changing a value is a deliberate, reviewed
edit to this file, never a runtime-learned value.
"""
from __future__ import annotations

from bujji.msi_strategy_eligibility import taxonomy as _sei_taxonomy

from . import taxonomy

SCHEMA_VERSION = taxonomy.TII_VERSION

DEFAULT_PROVENANCE = "msi_trade_intent.engine.determine_trade_intent"

# ---------------------------------------------------------------------------
# Check 1's disclosed placeholder family-selection priority order.
#
# This is NOT a real strategy selector: no scoring, no optimization, no
# preference for one family's expected quality over another's. It is a
# fixed, deterministic ALPHABETICAL tie-break over
# `StrategyEligibilityAssessment.eligible_strategy_families`, applied
# by `engine._placeholder_select_one_eligible_family`, purely so this
# sprint's Trade Intent stage has one concrete family to build intent
# around. A real Strategy Selector (Series 84+) would replace this
# with an actual, reasoned selection policy.
# ---------------------------------------------------------------------------
FAMILY_SELECTION_PRIORITY_ORDER = tuple(sorted(_sei_taxonomy.ALL_STRATEGY_FAMILIES))

# ---------------------------------------------------------------------------
# Per-family intent profile: (volatility_bias, premium_exposure,
# directional_exposure, risk_profile). A deterministic, disclosed
# mapping from conventional options-structure domain knowledge for
# each family -- never fit or learned. `market_bias` is handled
# separately by `engine.derive_market_bias` (see Check 1b -- honestly
# always DELTA_NEUTRAL today, absent a real upstream directional-lean
# field).
# ---------------------------------------------------------------------------
FAMILY_INTENT_PROFILE = {
    _sei_taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL: (
        taxonomy.VOLATILITY_BIAS_NEUTRAL, taxonomy.PREMIUM_EXPOSURE_PAYMENT,
        taxonomy.DIRECTIONAL_EXPOSURE_DEFINED, taxonomy.RISK_PROFILE_DEFINED,
    ),
    _sei_taxonomy.FAMILY_DEFINED_RISK_NEUTRAL: (
        taxonomy.VOLATILITY_BIAS_SHORT, taxonomy.PREMIUM_EXPOSURE_COLLECTION,
        taxonomy.DIRECTIONAL_EXPOSURE_DEFINED, taxonomy.RISK_PROFILE_DEFINED,
    ),
    _sei_taxonomy.FAMILY_UNDEFINED_RISK_PREMIUM: (
        taxonomy.VOLATILITY_BIAS_SHORT, taxonomy.PREMIUM_EXPOSURE_COLLECTION,
        taxonomy.DIRECTIONAL_EXPOSURE_UNDEFINED, taxonomy.RISK_PROFILE_UNDEFINED,
    ),
    _sei_taxonomy.FAMILY_LONG_VOLATILITY: (
        taxonomy.VOLATILITY_BIAS_LONG, taxonomy.PREMIUM_EXPOSURE_PAYMENT,
        taxonomy.DIRECTIONAL_EXPOSURE_DEFINED, taxonomy.RISK_PROFILE_DEFINED,
    ),
    _sei_taxonomy.FAMILY_SHORT_VOLATILITY: (
        taxonomy.VOLATILITY_BIAS_SHORT, taxonomy.PREMIUM_EXPOSURE_COLLECTION,
        taxonomy.DIRECTIONAL_EXPOSURE_UNDEFINED, taxonomy.RISK_PROFILE_UNDEFINED,
    ),
    _sei_taxonomy.FAMILY_CALENDAR: (
        taxonomy.VOLATILITY_BIAS_LONG, taxonomy.PREMIUM_EXPOSURE_PAYMENT,
        taxonomy.DIRECTIONAL_EXPOSURE_DEFINED, taxonomy.RISK_PROFILE_DEFINED,
    ),
    _sei_taxonomy.FAMILY_DIAGONAL: (
        taxonomy.VOLATILITY_BIAS_LONG, taxonomy.PREMIUM_EXPOSURE_PAYMENT,
        taxonomy.DIRECTIONAL_EXPOSURE_DEFINED, taxonomy.RISK_PROFILE_DEFINED,
    ),
    _sei_taxonomy.FAMILY_HEDGED_DIRECTIONAL: (
        taxonomy.VOLATILITY_BIAS_NEUTRAL, taxonomy.PREMIUM_EXPOSURE_NEUTRAL,
        taxonomy.DIRECTIONAL_EXPOSURE_HEDGED, taxonomy.RISK_PROFILE_DEFINED,
    ),
}

# Eligibility-confidence floor below which invalidation must flag the
# assumption as fragile (used by derive_invalidation_conditions -- a
# fixed, disclosed threshold, referencing
# bujji.msi_strategy_eligibility.taxonomy.ALL_ELIGIBILITY_CONFIDENCE_LEVELS).
MIN_ELIGIBILITY_CONFIDENCE_FOR_STABLE_INTENT = _sei_taxonomy.ELIGIBILITY_CONFIDENCE_MODERATE
