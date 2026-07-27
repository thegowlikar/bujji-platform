"""bujji.msi_position_recomposition.config — Series 110.

Structural, disclosed, never-tuned constants.
"""
from __future__ import annotations

DEFAULT_PROVENANCE = "bujji.msi_position_recomposition.engine"
SCHEMA_VERSION = "1.0.0"

# When the trigger is EXPIRY_ROLL, Trade Construction (Series 90, frozen)
# is re-invoked with `min_dte` pushed just past the current position's
# own DTE, so it cannot re-select the SAME expiry that is being rolled
# away from -- a real, structural use of Series 90's own already-exposed
# `min_dte` keyword argument, not a new construction rule.
EXPIRY_ROLL_MIN_DTE_BUFFER = 1
