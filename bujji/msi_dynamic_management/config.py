"""bujji.msi_dynamic_management.config — Series 109.

Structural, disclosed, never-tuned constants. Where a threshold reflects
one documented convention rather than a universal law, that is stated
here and in docs/DYNAMIC_MANAGEMENT.md's literature section
(Deliverable 1).
"""
from __future__ import annotations

DEFAULT_PROVENANCE = "bujji.msi_dynamic_management.engine"
SCHEMA_VERSION = "1.0.0"

# --- Strike roll priority bands ---------------------------------------------
# Reuses `msi_strategy_optimization.config.TESTED_STRIKE_DELTA_THRESHOLD`
# (0.35) BY IDENTITY as the RECOMMENDED boundary. These two additional
# bands are new to this package, both structural/disclosed:
STRIKE_ROLL_MANDATORY_DELTA = 0.50   # short option now more likely ITM than OTM -- a widely-cited "hard tested" line.
STRIKE_ROLL_OPTIONAL_BAND = 0.10     # within this much of the RECOMMENDED threshold -> OPTIONAL, not yet AVOID.

# --- Expiry roll priority bands ---------------------------------------------
# Reuses `msi_strategy_optimization.config.ROLL_EXPIRY_DTE_THRESHOLD`
# (21, style-specific -- see doc) as the RECOMMENDED boundary.
EXPIRY_ROLL_MANDATORY_DTE = 5        # near-universally cited gamma/pin-risk acceleration zone.
EXPIRY_ROLL_OPTIONAL_DTE = 35        # beyond this, expiry pressure is not yet worth flagging even as OPTIONAL.

# --- Wing adjustment: IV-regime-shift sensitivity ---------------------------
# A regime LABEL change (e.g. STABLE -> ELEVATED) between entry and now is
# used as the real, structural trigger for wing-adjustment consideration --
# never a numeric IV threshold invented here (VSB's own regime taxonomy,
# Series 88, frozen, is reused by identity instead of a new number).
