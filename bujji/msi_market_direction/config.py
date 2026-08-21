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


# --- Futures basis lens ----------------------------------------------------
# BASIS LEVEL IS NOT DIRECTIONAL. NIFTY futures normally trade at a premium
# that decays toward expiry, so "positive basis = bullish" would report a
# bullish market every morning of every cycle. The signal is the CHANGE:
# premium widening means futures buyers are paying up relative to spot,
# narrowing (or flipping to discount) means the opposite.
#
# THESE THRESHOLDS ARE DISCLOSED CONFIGURATION, NOT MEASURED. They are a
# modelled judgement about what separates a real basis move from quote noise
# on NIFTY, in index points, and have NOT been calibrated against outcomes --
# the same posture as execution_profiles NORMAL, which says so about
# slippage. Calibrating them needs the basis series the depth/futures capture
# is now accumulating.
BASIS_CHANGE_MIN_POINTS = 2.0     # below this, no opinion -- quote noise
BASIS_CHANGE_STRONG_POINTS = 10.0  # at or above this, a STRONG lean
