"""Decision Auditor & Learning Observatory taxonomy — Series 99. Plain
string constants (house convention, never enum.Enum)."""
from __future__ import annotations

MSI_DECISION_AUDITOR_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# --- Deliverable 3: decision outcome, at the top level --------------------
DECISION_TRADE_APPROVED = "TRADE_APPROVED"
DECISION_NO_TRADE = "NO_TRADE"

ALL_DECISION_OUTCOMES = (DECISION_TRADE_APPROVED, DECISION_NO_TRADE)

# --- Deliverable 4: realised direction -------------------------------------
REALISED_UP = "UP"
REALISED_DOWN = "DOWN"
REALISED_FLAT = "FLAT"

ALL_REALISED_DIRECTIONS = (REALISED_UP, REALISED_DOWN, REALISED_FLAT)

# --- Deliverable 4: execution feasibility ----------------------------------
FEASIBILITY_PLAN_PRODUCED = "PLAN_PRODUCED"
FEASIBILITY_NO_PLAN = "NO_PLAN"

ALL_FEASIBILITY_STATES = (FEASIBILITY_PLAN_PRODUCED, FEASIBILITY_NO_PLAN)

# --- Deliverable 4/5: thesis survival ---------------------------------------
SURVIVAL_UNKNOWN = "UNKNOWN"  # no next-day thesis available yet in the corpus (e.g. last real day).
