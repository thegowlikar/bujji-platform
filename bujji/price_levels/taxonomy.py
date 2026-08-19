"""Price Levels — controlled vocabulary.

Every string this package can emit is named here, so a consumer can switch on
values that exist rather than on values it hopes exist. Same discipline as
msi_price_structure/taxonomy.py, which this package sits beside rather than
inside: PSI answers "what SHAPE is price in" categorically; this package
answers "at what PRICE does structure sit" numerically. They are different
questions and neither subsumes the other.
"""
from __future__ import annotations

SCHEMA_VERSION = "1.0.0"

# --- What a level is ------------------------------------------------------
LEVEL_SWING_HIGH = "SWING_HIGH"
LEVEL_SWING_LOW = "SWING_LOW"
ALL_LEVEL_KINDS = (LEVEL_SWING_HIGH, LEVEL_SWING_LOW)

# --- How well tested it is ------------------------------------------------
# Buckets over a REAL touch count, published alongside the raw integer --
# never instead of it. The bucket is for switching, the count is the evidence.
STRENGTH_UNTESTED = "UNTESTED"      # formed, never revisited
STRENGTH_TESTED = "TESTED"          # revisited once or twice
STRENGTH_STRONG = "STRONG"          # revisited three or more times
ALL_STRENGTHS = (STRENGTH_UNTESTED, STRENGTH_TESTED, STRENGTH_STRONG)

# --- Whether we could answer at all ---------------------------------------
# ABSENCE IS NOT EMPTINESS. "No levels near price" and "we could not compute
# levels" are different facts and must never render identically -- the same
# rule D-5 applies to `families_assessed: None` vs `0`.
LEVELS_AVAILABLE = "AVAILABLE"
LEVELS_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
LEVELS_NO_AGREEMENT = "NO_AGREEMENT"   # swings found, none survived every parameter
ALL_LEVELS_STATUSES = (LEVELS_AVAILABLE, LEVELS_INSUFFICIENT_HISTORY, LEVELS_NO_AGREEMENT)

# --- Defaults -------------------------------------------------------------
# The detection parameters a swing must survive. A pivot visible at strength 3
# but gone at strength 8 is a parameter artifact, not a level: the same
# failure the regime stability gate already guards against, where derived
# structure moved with the sampling interval alone. Unanimity is deliberate
# and expected to be expensive in level count.
DEFAULT_SWING_STRENGTHS = (3, 5, 8)

# A touch is price entering a band around the level, not an exact equality --
# an exact float match would count almost nothing. 0.05% of the level price
# (about 12 points at NIFTY 24,000) is the disclosed default.
DEFAULT_TOUCH_TOLERANCE_FRACTION = 0.0005


# =========================================================================
# L-2: SUPPLY AND DEMAND ZONES
# =========================================================================
# A level is one price. A zone is the RANGE a move originated from -- the
# band price left behind when it went somewhere in a hurry. For a premium
# seller the distinction matters: a strike sits inside or outside a band,
# not exactly on a number.

ZONE_SUPPLY = "SUPPLY"        # origin of an impulse DOWN -- price above may meet sellers
ZONE_DEMAND = "DEMAND"        # origin of an impulse UP   -- price below may meet buyers
ALL_ZONE_KINDS = (ZONE_SUPPLY, ZONE_DEMAND)

# A zone's life. FRESH is not "better", it is "untested" -- an untested zone
# is an untested claim, and saying so is the point of keeping them apart.
ZONE_FRESH = "FRESH"          # never revisited since it formed
ZONE_TESTED = "TESTED"        # price returned into the band and did not close through
ZONE_BROKEN = "BROKEN"        # price CLOSED through the far side -- the claim failed
ALL_ZONE_STATUSES = (ZONE_FRESH, ZONE_TESTED, ZONE_BROKEN)

# The impulse thresholds a zone must survive, as multiples of the series'
# own typical bar range. Same gate as swings, different parameter: a zone
# that exists only because the threshold was set at 1.5x is an artifact of
# that choice, not a place the market cared about.
DEFAULT_IMPULSE_MULTIPLES = (1.5, 2.0, 3.0)

# Bars used to measure what a "typical" range is for THIS series, so the
# threshold scales with the instrument instead of being a hardcoded point
# value that means one thing at NIFTY 8,000 and another at 24,000.
DEFAULT_RANGE_LOOKBACK = 20
