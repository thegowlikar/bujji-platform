"""Strategy Expression Engine config — Series 93.

Deliverable 1 finding: Strategy Selection Foundation's own 13 real
strategy families (`bujji.msi_strategy_selection_foundation.taxonomy.STRATEGY_DEFINITIONS`)
already declare risk profile (also independently confirmed in
`bujji.msi_trade_construction.taxonomy.DEFINED_RISK_FAMILIES` /
`UNDEFINED_RISK_FAMILIES`, reused here verbatim rather than
re-classified) but NOT direction/volatility/theta/convexity exposure --
those are genuinely new classifications, declared once here from
standard options theory (never fit to replay data, never duplicated
from any existing module).

Also disclosed: the user's own illustrative examples ("Bull Call
Spread", "Debit Spread") do not correspond to any of SSF's real 13
families -- SSF has no distinct vertical-debit-spread family today.
`compatible_strategy_families` below is therefore always a subset of
SSF's REAL 13 families, never a fabricated name -- see
docs/STRATEGY_EXPRESSION_ENGINE.md Section on known limitations.
"""
from __future__ import annotations

from . import taxonomy as t

# Reused verbatim from bujji.msi_trade_construction.taxonomy (not
# re-derived) -- see module docstring.
_DEFINED_RISK_FAMILIES = (
    "IRON_CONDOR", "IRON_FLY", "BUTTERFLY", "CALENDAR",
    "LONG_DIRECTIONAL", "NEUTRAL_PREMIUM_BUYING", "VOLATILITY_EXPANSION",
)
_UNDEFINED_RISK_FAMILIES = (
    "SHORT_DIRECTIONAL", "NEUTRAL_PREMIUM_SELLING", "VOLATILITY_COMPRESSION",
    "COVERED", "RATIO", "SYNTHETIC",
)

# Declarative, standard-options-theory characteristics per SSF family --
# a NEW classification (Deliverable 1: no existing module declares
# direction/volatility/theta/convexity exposure), never fit to replay
# outcomes. Risk profile alone is reused verbatim from the sets above.
FAMILY_CHARACTERISTICS = {
    "LONG_DIRECTIONAL": frozenset({
        t.CHAR_DIRECTIONAL, t.CHAR_LONG_VOLATILITY, t.CHAR_NEGATIVE_THETA, t.CHAR_DEFINED_RISK,
        t.CHAR_DEBIT, t.CHAR_LIMITED_LOSS, t.CHAR_UNLIMITED_PROFIT, t.CHAR_POSITIVE_CONVEXITY,
    }),
    "SHORT_DIRECTIONAL": frozenset({
        t.CHAR_DIRECTIONAL, t.CHAR_SHORT_VOLATILITY, t.CHAR_POSITIVE_THETA, t.CHAR_UNDEFINED_RISK,
        t.CHAR_CREDIT, t.CHAR_UNLIMITED_LOSS, t.CHAR_LIMITED_PROFIT, t.CHAR_NEGATIVE_CONVEXITY,
    }),
    "NEUTRAL_PREMIUM_SELLING": frozenset({
        t.CHAR_DELTA_NEUTRAL, t.CHAR_SHORT_VOLATILITY, t.CHAR_POSITIVE_THETA, t.CHAR_UNDEFINED_RISK,
        t.CHAR_CREDIT, t.CHAR_UNLIMITED_LOSS, t.CHAR_LIMITED_PROFIT, t.CHAR_NEGATIVE_CONVEXITY,
    }),
    "NEUTRAL_PREMIUM_BUYING": frozenset({
        t.CHAR_DELTA_NEUTRAL, t.CHAR_LONG_VOLATILITY, t.CHAR_NEGATIVE_THETA, t.CHAR_DEFINED_RISK,
        t.CHAR_DEBIT, t.CHAR_LIMITED_LOSS, t.CHAR_UNLIMITED_PROFIT, t.CHAR_POSITIVE_CONVEXITY,
    }),
    "VOLATILITY_EXPANSION": frozenset({
        t.CHAR_DELTA_NEUTRAL, t.CHAR_LONG_VOLATILITY, t.CHAR_NEGATIVE_THETA, t.CHAR_DEFINED_RISK,
        t.CHAR_DEBIT, t.CHAR_LIMITED_LOSS, t.CHAR_UNLIMITED_PROFIT, t.CHAR_POSITIVE_CONVEXITY,
    }),
    "VOLATILITY_COMPRESSION": frozenset({
        t.CHAR_DELTA_NEUTRAL, t.CHAR_SHORT_VOLATILITY, t.CHAR_POSITIVE_THETA, t.CHAR_UNDEFINED_RISK,
        t.CHAR_CREDIT, t.CHAR_UNLIMITED_LOSS, t.CHAR_LIMITED_PROFIT, t.CHAR_NEGATIVE_CONVEXITY,
    }),
    "IRON_CONDOR": frozenset({
        t.CHAR_DELTA_NEUTRAL, t.CHAR_SHORT_VOLATILITY, t.CHAR_POSITIVE_THETA, t.CHAR_DEFINED_RISK,
        t.CHAR_CREDIT, t.CHAR_LIMITED_LOSS, t.CHAR_LIMITED_PROFIT, t.CHAR_NEGATIVE_CONVEXITY,
    }),
    "IRON_FLY": frozenset({
        t.CHAR_DELTA_NEUTRAL, t.CHAR_SHORT_VOLATILITY, t.CHAR_POSITIVE_THETA, t.CHAR_DEFINED_RISK,
        t.CHAR_CREDIT, t.CHAR_LIMITED_LOSS, t.CHAR_LIMITED_PROFIT, t.CHAR_NEGATIVE_CONVEXITY,
    }),
    "BUTTERFLY": frozenset({
        t.CHAR_DELTA_NEUTRAL, t.CHAR_SHORT_VOLATILITY, t.CHAR_POSITIVE_THETA, t.CHAR_DEFINED_RISK,
        t.CHAR_DEBIT, t.CHAR_LIMITED_LOSS, t.CHAR_LIMITED_PROFIT, t.CHAR_NEGATIVE_CONVEXITY,
    }),
    "RATIO": frozenset({
        t.CHAR_DIRECTIONAL, t.CHAR_SHORT_VOLATILITY, t.CHAR_POSITIVE_THETA, t.CHAR_UNDEFINED_RISK,
        t.CHAR_CREDIT, t.CHAR_UNLIMITED_LOSS, t.CHAR_LIMITED_PROFIT, t.CHAR_NEGATIVE_CONVEXITY,
    }),
    "COVERED": frozenset({
        t.CHAR_DIRECTIONAL, t.CHAR_SHORT_VOLATILITY, t.CHAR_POSITIVE_THETA, t.CHAR_UNDEFINED_RISK,
        t.CHAR_CREDIT, t.CHAR_UNLIMITED_LOSS, t.CHAR_LIMITED_PROFIT, t.CHAR_NEGATIVE_CONVEXITY,
    }),
    "SYNTHETIC": frozenset({
        t.CHAR_DIRECTIONAL, t.CHAR_UNDEFINED_RISK, t.CHAR_UNLIMITED_LOSS, t.CHAR_UNLIMITED_PROFIT,
    }),
    "CALENDAR": frozenset({
        t.CHAR_DELTA_NEUTRAL, t.CHAR_LONG_VOLATILITY, t.CHAR_POSITIVE_THETA, t.CHAR_DEFINED_RISK,
        t.CHAR_DEBIT, t.CHAR_LIMITED_LOSS, t.CHAR_LIMITED_PROFIT, t.CHAR_POSITIVE_CONVEXITY,
    }),
}

assert set(FAMILY_CHARACTERISTICS) == set(_DEFINED_RISK_FAMILIES) | set(_UNDEFINED_RISK_FAMILIES)
for _fam, _chars in FAMILY_CHARACTERISTICS.items():
    _expected_risk = t.CHAR_DEFINED_RISK if _fam in _DEFINED_RISK_FAMILIES else t.CHAR_UNDEFINED_RISK
    assert _expected_risk in _chars, f"{_fam} characteristics disagree with reused risk-profile source"

# --- Deliverable 4: per-thesis desired-exposure rules, declarative,
# never tuned against replay outcomes. Each entry: (desired_direction,
# desired_volatility_exposure, desired_risk_profile, desired_time_decay,
# desired_convexity, required_characteristics, forbidden_characteristics).
# ---------------------------------------------------------------------------
_DIRECTIONAL_RULE = (
    t.DIRECTION_DIRECTIONAL, t.VOLATILITY_EXPOSURE_LONG, t.RISK_PROFILE_DEFINED,
    t.TIME_DECAY_NEGATIVE, t.CONVEXITY_POSITIVE,
    (t.CHAR_DIRECTIONAL, t.CHAR_DEFINED_RISK, t.CHAR_POSITIVE_CONVEXITY),
    (t.CHAR_UNDEFINED_RISK, t.CHAR_NEGATIVE_CONVEXITY),
)

THESIS_EXPRESSION_RULES = {
    "TREND_CONTINUATION": _DIRECTIONAL_RULE,
    "TREND_REVERSAL": _DIRECTIONAL_RULE,
    "BREAKOUT": _DIRECTIONAL_RULE,
    "FAILED_BREAKOUT": _DIRECTIONAL_RULE,
    "MEAN_REVERSION": (
        t.DIRECTION_DIRECTIONAL, t.VOLATILITY_EXPOSURE_EITHER, t.RISK_PROFILE_DEFINED,
        t.TIME_DECAY_EITHER, t.CONVEXITY_EITHER,
        (t.CHAR_DIRECTIONAL, t.CHAR_DEFINED_RISK), (t.CHAR_UNDEFINED_RISK,),
    ),
    "RANGE_PERSISTENCE": (
        t.DIRECTION_DELTA_NEUTRAL, t.VOLATILITY_EXPOSURE_SHORT, t.RISK_PROFILE_EITHER,
        t.TIME_DECAY_POSITIVE, t.CONVEXITY_NEGATIVE,
        (t.CHAR_DELTA_NEUTRAL, t.CHAR_SHORT_VOLATILITY), (t.CHAR_DIRECTIONAL, t.CHAR_LONG_VOLATILITY),
    ),
    "VOLATILITY_EXPANSION": (
        t.DIRECTION_EITHER, t.VOLATILITY_EXPOSURE_LONG, t.RISK_PROFILE_EITHER,
        t.TIME_DECAY_EITHER, t.CONVEXITY_POSITIVE,
        (t.CHAR_LONG_VOLATILITY, t.CHAR_POSITIVE_CONVEXITY), (t.CHAR_SHORT_VOLATILITY, t.CHAR_NEGATIVE_CONVEXITY),
    ),
    "VOLATILITY_COMPRESSION": (
        t.DIRECTION_EITHER, t.VOLATILITY_EXPOSURE_SHORT, t.RISK_PROFILE_EITHER,
        t.TIME_DECAY_EITHER, t.CONVEXITY_NEGATIVE,
        (t.CHAR_SHORT_VOLATILITY, t.CHAR_NEGATIVE_CONVEXITY), (t.CHAR_LONG_VOLATILITY, t.CHAR_POSITIVE_CONVEXITY),
    ),
    "EVENT_RISK": (
        t.DIRECTION_EITHER, t.VOLATILITY_EXPOSURE_EITHER, t.RISK_PROFILE_DEFINED,
        t.TIME_DECAY_EITHER, t.CONVEXITY_EITHER,
        (t.CHAR_DEFINED_RISK,), (t.CHAR_UNDEFINED_RISK,),
    ),
    "NO_TRADE": (
        t.DIRECTION_UNKNOWN, t.VOLATILITY_EXPOSURE_UNKNOWN, t.RISK_PROFILE_UNKNOWN,
        t.TIME_DECAY_UNKNOWN, t.CONVEXITY_UNKNOWN, (), (),
    ),
}
