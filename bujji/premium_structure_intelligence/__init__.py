"""Phase 20.31 -- Premium Structure Intelligence Layer v1.

Answers exactly one question, sitting after "premium selling is
suitable" (msi_strategy_selection_foundation, real, validated Phase
20.28) and before construction (construction_shape_bridge, Phase
20.29; msi_trade_construction, protected): given today's real market
evidence, which of the three structures Bujji can genuinely construct
today (SHORT_STRANGLE, IRON_CONDOR, IRON_FLY) fits best -- or NO_TRADE.

STEP 1 AUDIT SUMMARY (Phase 20.31's own, building on Phases
20.28-20.30):

- `msi_strategy_selection_foundation`/`msi_strategy_selector` -- NOT
  duplicated. This package does not re-implement family selection, the
  13-family capability table, or the 10-state market ontology. It reads
  the same real upstream assessments those packages already consume
  (MDI/PSI/MSSI/VSB) and answers a narrower, disclosed structure-level
  question those packages were never designed to answer (confirmed,
  Phase 20.28/20.30: msi_strategy_selector's own scoring operates on
  FAMILIES via a 10-state ontology, never comparing SHORT_STRANGLE
  against IRON_CONDOR against IRON_FLY on the same footing).
- `msi_trade_construction` -- NOT modified. Still protected, still
  byte-identical to its locked baseline (verified by this phase's own
  tests, same discipline Phase 20.29 established after its own
  reverted first attempt).
- `construction_shape_bridge` (Phase 20.29) -- extended additively
  with one new sibling function; its existing
  `construct_trade_honoring_position_plan` is untouched.
- Rules are derived from Phase 20.30's real 45-day historical replay
  evidence (not invented): the BALANCE-vs-BALANCE+COMPRESSION
  distinction this package's IRON_FLY/SHORT_STRANGLE priority ordering
  encodes was directly observed differentiating real days in that
  replay (2026-07-20).

NEVER creates a trading signal fed back into any decision outside this
narrow question; NEVER imports a broker module; NEVER calls the Risk
Governor or Execution Intelligence; NEVER modifies FinalDecision,
evidence_score, or any upstream assessment. Not wired into any live
runtime by this phase -- Observation/Suitability -> Structure Choice
only, matching microstructure_intelligence's own Phase 20.27 boundary
discipline.
"""
from .engine import select_structure
from .explain import explain_structure_selection
from .models import CandidateEvaluation, Explanation, StructureSelectionAssessment
from .taxonomy import (
    ALL_STRUCTURE_TYPES, STRUCTURE_IRON_CONDOR, STRUCTURE_IRON_FLY,
    STRUCTURE_NO_TRADE, STRUCTURE_SHORT_STRANGLE,
)

__all__ = [
    "select_structure", "explain_structure_selection",
    "CandidateEvaluation", "Explanation", "StructureSelectionAssessment",
    "ALL_STRUCTURE_TYPES", "STRUCTURE_IRON_CONDOR", "STRUCTURE_IRON_FLY",
    "STRUCTURE_NO_TRADE", "STRUCTURE_SHORT_STRANGLE",
]
