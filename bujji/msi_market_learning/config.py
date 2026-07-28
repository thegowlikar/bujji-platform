"""MLE config — Series 100, Phase 1.0. Declarative thresholds only, never
tuned against real trading outcomes (house convention, mirrors every
other MSI config.py in this project)."""
from __future__ import annotations

from . import taxonomy as t

# Occurrence-count -> evidence tier ladder, exactly as specified in the
# Series 100 mission (1 / 5 / 20 / 75 / 200). A candidate's tier is the
# HIGHEST threshold its occurrence_count has reached or passed.
EVIDENCE_TIER_THRESHOLDS = (
    (1, t.TIER_NONE),
    (5, t.TIER_WEAK),
    (20, t.TIER_MODERATE),
    (75, t.TIER_STRONG),
    (200, t.TIER_ENGINEERING_CANDIDATE),
)

# Decay assessment: a real, disclosed, non-tunable rule -- compares the
# occurrence rate over the most recent window against the candidate's own
# lifetime average rate. Declared once, never fit to data.
DECAY_RECENT_WINDOW_OCCURRENCES = 10   # how many of the most recent occurrences count as "recent"
DECAY_IMPROVING_RATIO = 1.25           # recent rate >= 1.25x lifetime rate -> IMPROVING
DECAY_WEAKENING_RATIO = 0.75           # recent rate <= 0.75x lifetime rate -> WEAKENING

DEFAULT_PROVENANCE = "bujji.msi_market_learning.engine"
SCHEMA_VERSION = t.MSI_MARKET_LEARNING_VERSION
