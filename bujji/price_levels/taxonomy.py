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
