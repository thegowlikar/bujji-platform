"""Static configuration for Market Direction Intelligence. Every
threshold is fixed, disclosed configuration — never fit/tuned against
outcomes, mirroring bujji.msi_consensus.config's exact posture.
"""
from __future__ import annotations

from . import taxonomy

SCHEMA_VERSION = taxonomy.MSI_MARKET_DIRECTION_VERSION

DEFAULT_PROVENANCE = "msi_market_direction.engine.determine_market_direction"

# Minimum price-structure events required before the Price Structure
# lens will even attempt a directional read (below this, honestly
# UNKNOWN — insufficient evidence, not a fabricated lean).
PRICE_STRUCTURE_LENS_MIN_EVENTS = 2

# reconcile_lenses banding: an "opinionated" lens is one whose
# directional_lean is a real band value (not UNKNOWN). Overall
# confidence is a function of (opinionated_count, conflicting_count).
CONFIDENCE_ALL_AGREE_MIN_OPINIONATED = 2   # >=2 agreeing lenses -> HIGH is reachable.
