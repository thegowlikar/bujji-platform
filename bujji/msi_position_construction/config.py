"""Position Construction Intelligence config — Series 95.

Deliverable 1 reuse: expiry DTE window, per-family delta targets, and
wing-width policy are ALL imported directly from
`bujji.msi_trade_construction.config` -- never re-declared. Only the
NEW construction-type/adjustment-readiness policy tables below are new
to this package, because Series 90 has no notion of "which shape" (it
always builds exactly one hardcoded shape per family) -- that decision
did not exist anywhere before this series.
"""
from __future__ import annotations

from bujji.msi_trade_construction import config as tc_config

from . import taxonomy as t

# --- Reused directly (Deliverable 1) --------------------------------------
DEFAULT_MIN_DTE = tc_config.DEFAULT_MIN_DTE
DEFAULT_MAX_DTE = tc_config.DEFAULT_MAX_DTE
FAMILY_DELTA_TARGETS = tc_config.FAMILY_DELTA_TARGETS
WING_WIDTH_FALLBACK_POINTS = tc_config.WING_WIDTH_FALLBACK_POINTS
WING_WIDTH_EXPECTED_MOVE_MULTIPLIER = tc_config.WING_WIDTH_EXPECTED_MOVE_MULTIPLIER

# --- Deliverable 3: declarative default construction type per family
# (the natural, single shape Series 90 already builds for that family
# today) -- construction PHILOSOPHY, never exact strikes. ------------------
FAMILY_DEFAULT_CONSTRUCTION_TYPE = {
    "LONG_DIRECTIONAL": t.CONSTRUCTION_SINGLE_LEG,
    "SHORT_DIRECTIONAL": t.CONSTRUCTION_SINGLE_LEG,
    "NEUTRAL_PREMIUM_SELLING": t.CONSTRUCTION_SHORT_STRANGLE,
    "NEUTRAL_PREMIUM_BUYING": t.CONSTRUCTION_LONG_STRANGLE,
    "VOLATILITY_EXPANSION": t.CONSTRUCTION_LONG_STRADDLE,
    "VOLATILITY_COMPRESSION": t.CONSTRUCTION_SHORT_STRADDLE,
    "IRON_CONDOR": t.CONSTRUCTION_IRON_CONDOR_SHAPE,
    "IRON_FLY": t.CONSTRUCTION_IRON_FLY_SHAPE,
    "BUTTERFLY": t.CONSTRUCTION_BUTTERFLY_SHAPE,
    "RATIO": t.CONSTRUCTION_RATIO_SHAPE,
    "COVERED": t.CONSTRUCTION_COVERED_SHAPE,
    "SYNTHETIC": t.CONSTRUCTION_SYNTHETIC_SHAPE,
    "CALENDAR": t.CONSTRUCTION_CALENDAR_SHAPE,
}

# --- Deliverable 3: construction REFINEMENT rules -- scoped narrowly to
# the two single-leg directional families, where a real, disclosed,
# non-tuned professional preference exists (reduce cost/theta bleed at
# lower conviction; cap a naked leg's risk when the expression demands
# defined risk). Every other family keeps its natural default shape --
# a deliberate, disclosed scope boundary (see docs Section on future
# extensions), never a blanket "always spread everything" rule. ---------
DIRECTIONAL_REFINEMENT_FAMILIES = ("LONG_DIRECTIONAL", "SHORT_DIRECTIONAL")

# Below this conviction, LONG_DIRECTIONAL prefers a cheaper, lower-theta
# vertical debit spread over a naked single long option -- reduces cost
# when the belief is real but not maximal, a real professional-desk
# convention, never fit to replay P&L.
DEBIT_SPREAD_BELOW_CONVICTION = "HIGH"  # i.e. MODERATE or LOW -> spread; HIGH -> single leg.

# --- Wing-bearing construction types (used to decide wing_plan) ---------
WING_BEARING_CONSTRUCTION_TYPES = (
    t.CONSTRUCTION_IRON_CONDOR_SHAPE, t.CONSTRUCTION_IRON_FLY_SHAPE, t.CONSTRUCTION_BUTTERFLY_SHAPE,
)

# --- Adjustment readiness per construction type (NEW, declarative) ------
ADJUSTMENT_READINESS_BY_CONSTRUCTION_TYPE = {
    t.CONSTRUCTION_SINGLE_LEG: t.ADJUSTMENT_LIMITED,
    t.CONSTRUCTION_VERTICAL_DEBIT_SPREAD: t.ADJUSTMENT_FRIENDLY,
    t.CONSTRUCTION_VERTICAL_CREDIT_SPREAD: t.ADJUSTMENT_FRIENDLY,
    t.CONSTRUCTION_SHORT_STRANGLE: t.ADJUSTMENT_FRIENDLY,
    t.CONSTRUCTION_LONG_STRANGLE: t.ADJUSTMENT_LIMITED,
    t.CONSTRUCTION_LONG_STRADDLE: t.ADJUSTMENT_LIMITED,
    t.CONSTRUCTION_SHORT_STRADDLE: t.ADJUSTMENT_FRIENDLY,
    t.CONSTRUCTION_IRON_CONDOR_SHAPE: t.ADJUSTMENT_FRIENDLY,
    t.CONSTRUCTION_IRON_FLY_SHAPE: t.ADJUSTMENT_FRIENDLY,
    t.CONSTRUCTION_BUTTERFLY_SHAPE: t.ADJUSTMENT_LIMITED,
    t.CONSTRUCTION_RATIO_SHAPE: t.ADJUSTMENT_LIMITED,
    t.CONSTRUCTION_COVERED_SHAPE: t.ADJUSTMENT_FRIENDLY,
    t.CONSTRUCTION_SYNTHETIC_SHAPE: t.ADJUSTMENT_LIMITED,
    t.CONSTRUCTION_CALENDAR_SHAPE: t.ADJUSTMENT_FRIENDLY,
}

# --- Payoff profile per construction type (declarative, standard options
# theory -- same real-world facts as msi_strategy_expression's own
# FAMILY_CHARACTERISTICS table, re-expressed at the construction-type
# level since a refined shape can change the payoff -- e.g. a naked
# SINGLE_LEG short call has unlimited loss, but its VERTICAL_CREDIT_SPREAD
# refinement caps it). ----------------------------------------------------
PAYOFF_BY_CONSTRUCTION_TYPE = {
    t.CONSTRUCTION_SINGLE_LEG: t.PAYOFF_UNLIMITED_PROFIT_LIMITED_LOSS,  # long option default; overridden for naked shorts in engine.py.
    t.CONSTRUCTION_VERTICAL_DEBIT_SPREAD: t.PAYOFF_LIMITED_PROFIT_LIMITED_LOSS,
    t.CONSTRUCTION_VERTICAL_CREDIT_SPREAD: t.PAYOFF_LIMITED_PROFIT_LIMITED_LOSS,
    t.CONSTRUCTION_SHORT_STRANGLE: t.PAYOFF_LIMITED_PROFIT_UNLIMITED_LOSS,
    t.CONSTRUCTION_LONG_STRANGLE: t.PAYOFF_UNLIMITED_PROFIT_LIMITED_LOSS,
    t.CONSTRUCTION_LONG_STRADDLE: t.PAYOFF_UNLIMITED_PROFIT_LIMITED_LOSS,
    t.CONSTRUCTION_SHORT_STRADDLE: t.PAYOFF_LIMITED_PROFIT_UNLIMITED_LOSS,
    t.CONSTRUCTION_IRON_CONDOR_SHAPE: t.PAYOFF_LIMITED_PROFIT_LIMITED_LOSS,
    t.CONSTRUCTION_IRON_FLY_SHAPE: t.PAYOFF_LIMITED_PROFIT_LIMITED_LOSS,
    t.CONSTRUCTION_BUTTERFLY_SHAPE: t.PAYOFF_LIMITED_PROFIT_LIMITED_LOSS,
    t.CONSTRUCTION_RATIO_SHAPE: t.PAYOFF_LIMITED_PROFIT_UNLIMITED_LOSS,
    t.CONSTRUCTION_COVERED_SHAPE: t.PAYOFF_LIMITED_PROFIT_UNLIMITED_LOSS,
    t.CONSTRUCTION_SYNTHETIC_SHAPE: t.PAYOFF_UNKNOWN,  # unbounded both ways depending on side -- not a clean LIMITED/UNLIMITED pair.
    t.CONSTRUCTION_CALENDAR_SHAPE: t.PAYOFF_LIMITED_PROFIT_LIMITED_LOSS,
}
