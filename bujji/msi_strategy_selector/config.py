"""Static configuration for the Strategy Selector. No tuning against
historical outcomes -- every constant is fixed, disclosed data mirroring
every prior MSI package's posture."""
from __future__ import annotations

from . import taxonomy

SCHEMA_VERSION = taxonomy.MSI_STRATEGY_SELECTOR_VERSION
DEFAULT_PROVENANCE = "msi_strategy_selector.engine.select_strategy"

# Confidence banding: how many suitable, non-disqualified candidates
# existed, and how decisively the winner separated from the runner-up.
CONFIDENCE_HIGH_MIN_SCORE_MARGIN = 2   # winner beats runner-up by at least this many match-score points.
