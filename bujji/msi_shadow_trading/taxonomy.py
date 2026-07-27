"""Shadow Trading Engine taxonomy — Series 100. Plain string constants
(house convention, never enum.Enum)."""
from __future__ import annotations

MSI_SHADOW_TRADING_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# --- Exit reasons -- ALL reused directly from Position Lifecycle's own
# real position_state vocabulary (bujji.msi_position_lifecycle.taxonomy),
# never a shadow-only invented rule. `HARD_SESSION_CLOSE` is the one
# genuinely new label, used only when a position reaches its own real
# `position_close_date` with no other exit yet recorded -- itself just a
# restatement of Position Lifecycle's own CLOSED state, not new logic. --
EXIT_REASON_THESIS_BROKEN = "THESIS_BROKEN"
EXIT_REASON_EXIT_CANDIDATE = "EXIT_CANDIDATE"
EXIT_REASON_PROFIT_HARVEST = "PROFIT_HARVEST"
EXIT_REASON_HARD_SESSION_CLOSE = "HARD_SESSION_CLOSE"

ALL_EXIT_REASONS = (
    EXIT_REASON_THESIS_BROKEN, EXIT_REASON_EXIT_CANDIDATE,
    EXIT_REASON_PROFIT_HARVEST, EXIT_REASON_HARD_SESSION_CLOSE,
)

# Position Lifecycle states that trigger a shadow exit -- reused directly,
# never a new taxonomy of "shadow" states.
EXIT_TRIGGERING_LIFECYCLE_STATES = ("THESIS_BROKEN", "EXIT_CANDIDATE", "PROFIT_HARVEST", "CLOSED")
