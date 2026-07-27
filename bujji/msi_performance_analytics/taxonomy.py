"""Performance Analytics & Edge Validation taxonomy — Series 101.
Plain string constants (house convention, never enum.Enum)."""
from __future__ import annotations

MSI_PERFORMANCE_ANALYTICS_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# --- Deliverable 3: decision categories, finer-grained than DALO's own
# binary TRADE_APPROVED/NO_TRADE (a real, derived split for reporting
# only -- DALO's own field remains authoritative). ------------------------
CATEGORY_APPROVED = "APPROVED"
CATEGORY_REJECTED = "REJECTED"          # a family was selected but Portfolio Construction rejected it.
CATEGORY_NO_TRADE = "NO_TRADE"          # no strategy family was ever selected.

ALL_DECISION_CATEGORIES = (CATEGORY_APPROVED, CATEGORY_REJECTED, CATEGORY_NO_TRADE)

# --- Deliverable 7: statistical reliability ------------------------------
RELIABILITY_RELIABLE = "RELIABLE"
RELIABILITY_NOT_RELIABLE = "NOT_STATISTICALLY_RELIABLE"

ALL_RELIABILITY_STATES = (RELIABILITY_RELIABLE, RELIABILITY_NOT_RELIABLE)

# --- Deliverable 6: counterfactual classification for a rejected/no-trade
# decision, given what actually happened afterward. -----------------------
COUNTERFACTUAL_REJECTED_WINNER = "REJECTED_WINNER"    # would have been profitable.
COUNTERFACTUAL_REJECTED_LOSER = "REJECTED_LOSER"      # would have been unprofitable.
COUNTERFACTUAL_UNKNOWN = "UNKNOWN"                     # insufficient real data to judge.

ALL_COUNTERFACTUAL_CLASSES = (COUNTERFACTUAL_REJECTED_WINNER, COUNTERFACTUAL_REJECTED_LOSER, COUNTERFACTUAL_UNKNOWN)
