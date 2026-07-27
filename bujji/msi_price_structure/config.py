"""Static configuration for the Price Structure Intelligence engine.

Per this project's "no tuning/optimization" constraint (MSI_V1_FOUNDATION.md
Guiding Principle 9), every threshold below is a FIXED, disclosed value,
never a learned/optimized parameter. Reasoning for each is given inline.
"""
from __future__ import annotations

from . import taxonomy

SCHEMA_VERSION = taxonomy.MSI_PRICE_STRUCTURE_VERSION

DEFAULT_DETECTION_CONTEXT_LIVE = "LIVE"
DEFAULT_DETECTION_CONTEXT_REPLAY = "REPLAY"
DEFAULT_DETECTION_CONTEXT_BATCH = "BATCH"

DEFAULT_ORIGINATING_SOURCE = "msi_price_structure.engine"
DEFAULT_PROVENANCE = "msi_price_structure.engine.assess_price_structure"

# ---------------------------------------------------------------------------
# Trend derivation thresholds — a "run" is a trailing sequence of
# same-signed price deltas. 2 in a row is the minimum a discretionary
# trader would call "an emerging directional lean"; 3+ is the minimum
# to call it "established" (matching this project's convention
# elsewhere of small, disclosed integer thresholds rather than
# statistical fits).
# ---------------------------------------------------------------------------
TREND_EMERGING_MIN_RUN = 2
TREND_ESTABLISHED_MIN_RUN = 3

# ---------------------------------------------------------------------------
# Compression/Expansion thresholds — a run of 2 consecutive
# shrinking (resp. growing) |delta| values is "EARLY", 3+ is
# "CONFIRMED". These are necessarily approximate: real range/
# volatility data (Volatility Structure brain, MSI Brain 3) does not
# exist yet, so |delta| of consecutive PRICE_CHANGED events is the
# only price-only proxy currently available. Disclosed in
# docs/MSI_PRICE_STRUCTURE_INTELLIGENCE.md and in every assessment's
# Explanation.missing_evidence.
# ---------------------------------------------------------------------------
COMPRESSION_EARLY_MIN_RUN = 2
COMPRESSION_CONFIRMED_MIN_RUN = 3
EXPANSION_EARLY_MIN_RUN = 2
EXPANSION_CONFIRMED_MIN_RUN = 3

# ---------------------------------------------------------------------------
# Balance thresholds — balance_ratio = |sum(deltas)| / sum(|delta|).
# 0.0 means price moved back and forth and net-cancelled (textbook
# balance); 1.0 means every move was the same direction (textbook
# imbalance/trend). Fixed cut points, not fit to any dataset:
#   ratio <  BALANCE_RATIO_LOW_THRESHOLD  -> IN_BALANCE
#   ratio >= BALANCE_RATIO_HIGH_THRESHOLD -> IMBALANCED
#   otherwise                              -> TRANSITIONING
# ---------------------------------------------------------------------------
BALANCE_RATIO_LOW_THRESHOLD = 0.35
BALANCE_RATIO_HIGH_THRESHOLD = 0.65

# ---------------------------------------------------------------------------
# Confidence derivation — evidence sufficiency thresholds (count of
# price-structure events actually available), before any contradiction
# penalty is applied. Mirrors Series 77's confidence-decreases-with-
# contradiction property (see engine.compute_confidence).
# ---------------------------------------------------------------------------
CONFIDENCE_EVIDENCE_THRESHOLDS = (
    (0, taxonomy.CONFIDENCE_NONE),
    (2, taxonomy.CONFIDENCE_LOW),
    (4, taxonomy.CONFIDENCE_MODERATE),
    (6, taxonomy.CONFIDENCE_HIGH),
)
