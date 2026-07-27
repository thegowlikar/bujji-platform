"""Static configuration for Volatility Structure Bridge. No new
tuning -- thresholds either come directly from the reused legacy
constants (disclosed, cited) or are the small, new ExpectedMoveState
bucketing thresholds (also disclosed in taxonomy.py)."""
from __future__ import annotations

from . import taxonomy

SCHEMA_VERSION = taxonomy.MSI_VOLATILITY_STRUCTURE_VERSION
DEFAULT_PROVENANCE = "msi_volatility_structure.engine.assess_volatility_structure"

DEFAULT_RISK_FREE_RATE = 0.065  # Same default as legacy VolatilityBrain/GreeksBrain.

MIN_CANDLES_FOR_REALIZED_VOL = 6  # Same floor as legacy VolatilityBrain/RegimeBrain.
