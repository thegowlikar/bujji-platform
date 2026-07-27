"""bujji.msi_position_recomposition.taxonomy — Series 110.

Plain string constants (this project's established convention).
"""
from __future__ import annotations

MSI_POSITION_RECOMPOSITION_VERSION = "1.0.0"
UNKNOWN = "UNKNOWN"

RECOMPOSITION_NOT_POSSIBLE = "RECOMPOSITION_NOT_POSSIBLE"

LEG_ACTION_KEEP = "KEEP"
LEG_ACTION_CLOSE = "CLOSE"
LEG_ACTION_OPEN = "OPEN"
ALL_LEG_ACTIONS = (LEG_ACTION_KEEP, LEG_ACTION_CLOSE, LEG_ACTION_OPEN)

# The trigger this recomposition was produced for -- one of Series 109's
# own real decision types, reused BY IDENTITY (never re-declared).
from bujji.msi_dynamic_management import taxonomy as _mdm_taxonomy  # noqa: E402

TRIGGER_STRIKE_ROLL = _mdm_taxonomy.DECISION_STRIKE_ROLL
TRIGGER_EXPIRY_ROLL = _mdm_taxonomy.DECISION_EXPIRY_ROLL
TRIGGER_DELTA_REBALANCE = _mdm_taxonomy.DECISION_DELTA_REBALANCE
TRIGGER_WING_ADJUSTMENT = _mdm_taxonomy.DECISION_WING_ADJUSTMENT
TRIGGER_STRATEGY_CONVERSION = _mdm_taxonomy.DECISION_STRATEGY_CONVERSION
TRIGGER_FULL_EXIT = _mdm_taxonomy.DECISION_FULL_EXIT
