"""Static configuration for the Market Structure Intelligence engine.

Every threshold below is a FIXED, disclosed value, never a learned/
optimized parameter — per MSI_V1_FOUNDATION.md Guiding Principle 9,
mirroring `bujji.msi_price_structure.config`'s exact discipline.
"""
from __future__ import annotations

from . import taxonomy

SCHEMA_VERSION = taxonomy.MSI_MARKET_STRUCTURE_VERSION

DEFAULT_DETECTION_CONTEXT_LIVE = "LIVE"
DEFAULT_DETECTION_CONTEXT_REPLAY = "REPLAY"
DEFAULT_DETECTION_CONTEXT_BATCH = "BATCH"

DEFAULT_ORIGINATING_SOURCE = "msi_market_structure.engine"
DEFAULT_PROVENANCE = "msi_market_structure.engine.assess_market_structure"

# ---------------------------------------------------------------------------
# Level identification — a local extreme (price strictly higher/lower
# than both immediate neighbors in the ordered price sequence) is a
# candidate level. This is the minimal, disclosed, price-only swing-
# point primitive available without real volume/OI participation data
# (Options/Futures/Liquidity brains do not exist yet) — the same
# "necessarily approximate, disclosed" posture Series 78 takes for
# compression/expansion.
# ---------------------------------------------------------------------------
LEVEL_PROXIMITY_FRACTION = 0.005   # Price within 0.5% of a level's price counts as "at" that level (a test/touch).

# ---------------------------------------------------------------------------
# Support/Resistance state thresholds — test count at a live
# (unbroken) level.
# ---------------------------------------------------------------------------
SUPPORT_WEAK_MIN_TESTS = 1
SUPPORT_DEVELOPING_MIN_TESTS = 2
SUPPORT_ESTABLISHED_MIN_TESTS = 3

RESISTANCE_WEAK_MIN_TESTS = 1
RESISTANCE_DEVELOPING_MIN_TESTS = 2
RESISTANCE_ESTABLISHED_MIN_TESTS = 3

# ---------------------------------------------------------------------------
# Breakout/Breakdown thresholds — a decisive break requires price to
# close beyond the level by at least this fraction of the level's
# price; CONFIRMED requires the break to be sustained (price stays
# beyond the level) for at least this many subsequent price events;
# FAILED means price returned back across the level within that
# window instead.
# ---------------------------------------------------------------------------
BREAK_DECISIVE_FRACTION = 0.003
BREAK_CONFIRM_MIN_SUSTAIN_EVENTS = 2

# ---------------------------------------------------------------------------
# Retest thresholds — after a break, price returning to within
# LEVEL_PROXIMITY_FRACTION of the broken level counts as an active
# retest; it CONFIRMS if price then continues away from the level in
# the breaking direction, FAILS if price instead crosses back through
# to the pre-break side.
# ---------------------------------------------------------------------------
RETEST_PROXIMITY_FRACTION = LEVEL_PROXIMITY_FRACTION

# ---------------------------------------------------------------------------
# Confidence — evidence sufficiency thresholds (count of identified
# structural levels with at least one test), before any contradiction
# penalty. Mirrors Series 78's confidence-decreases-with-contradiction
# property.
# ---------------------------------------------------------------------------
CONFIDENCE_EVIDENCE_THRESHOLDS = (
    (0, taxonomy.CONFIDENCE_NONE),
    (1, taxonomy.CONFIDENCE_LOW),
    (2, taxonomy.CONFIDENCE_MODERATE),
    (3, taxonomy.CONFIDENCE_HIGH),
)
