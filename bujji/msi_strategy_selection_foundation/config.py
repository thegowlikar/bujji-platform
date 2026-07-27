"""Static configuration for Strategy Selection Foundation. No
tuning/optimization -- every threshold below is a fixed, disclosed
value, mirroring every prior MSI package's posture."""
from __future__ import annotations

from . import taxonomy

SCHEMA_VERSION = taxonomy.MSI_STRATEGY_SELECTION_FOUNDATION_VERSION
DEFAULT_PROVENANCE = "msi_strategy_selection_foundation.engine.assess_strategy_suitability"

# Confidence banding thresholds (Deliverable 4): a strategy's overall
# confidence is a function of (consensus_level rank, positioning
# agreement with direction). Fixed, disclosed constants only.
CONSENSUS_RANK_FOR_HIGH_CONFIDENCE = 3  # STRONG_CONSENSUS or better.
CONSENSUS_RANK_FOR_MODERATE_CONFIDENCE = 2  # MODERATE_CONSENSUS or better.
