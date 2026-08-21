"""bujji.premium_structure_intelligence.taxonomy — Phase 20.31.

Plain string constants, never enum.Enum -- the same established
convention every msi_* taxonomy module in this codebase already uses.

Scope is deliberately narrow: this is NOT a general strategy taxonomy
(that already exists, msi_strategy_selection_foundation, 13 families).
This is the one decision that sits after "premium selling is
suitable" and before construction: WHICH of the three structures
`bujji.msi_trade_construction` can genuinely build today should be
used. SHORT_STRADDLE and neutral CREDIT_SPREAD are deliberately
excluded (Phase 20.29/20.30 findings: no real construction logic
exists for either as a neutral structure) -- adding them here would
let this layer select a shape nothing downstream can build.
"""
from __future__ import annotations

PREMIUM_STRUCTURE_INTELLIGENCE_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# --- StructureType ---------------------------------------------------------
STRUCTURE_SHORT_STRANGLE = "SHORT_STRANGLE"
STRUCTURE_IRON_CONDOR = "IRON_CONDOR"
STRUCTURE_IRON_FLY = "IRON_FLY"
STRUCTURE_NO_TRADE = "NO_TRADE"

ALL_STRUCTURE_TYPES = (STRUCTURE_SHORT_STRANGLE, STRUCTURE_IRON_CONDOR, STRUCTURE_IRON_FLY, STRUCTURE_NO_TRADE)

# --- ConfidenceLevel -- established NONE/LOW/MODERATE/HIGH convention -----
CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"

ALL_CONFIDENCE_LEVELS = (CONFIDENCE_NONE, CONFIDENCE_LOW, CONFIDENCE_MODERATE, CONFIDENCE_HIGH)

# --- DataQuality -- mirrors msi_strategy_selection_foundation's own
# SUITABLE/UNSUITABLE/INSUFFICIENT_EVIDENCE discipline: SUFFICIENT is a
# real, evidence-backed conclusion; INSUFFICIENT means required upstream
# evidence (mdi/psi/mssi/vsb) was genuinely absent this cycle -- never
# forced to SUFFICIENT on partial data. -------------------------------
DATA_QUALITY_SUFFICIENT = "SUFFICIENT"
DATA_QUALITY_INSUFFICIENT = "INSUFFICIENT"

ALL_DATA_QUALITY_VALUES = (DATA_QUALITY_SUFFICIENT, DATA_QUALITY_INSUFFICIENT)

# --- Rejection / no-trade reasons (Deliverable, fail closed) --------------
REJECT_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
REJECT_NOT_NEUTRAL = "NOT_NEUTRAL_DIRECTION"
REJECT_VOLATILITY_EXPANDING = "VOLATILITY_EXPANDING"
REJECT_NO_STRUCTURE_MATCHED = "NO_STRUCTURE_MATCHED"

ALL_NO_TRADE_REASONS = (
    REJECT_INSUFFICIENT_EVIDENCE, REJECT_NOT_NEUTRAL, REJECT_VOLATILITY_EXPANDING, REJECT_NO_STRUCTURE_MATCHED,
)
