"""Counterfactual Replay Engine (CRE) taxonomy — Series 102. Plain
string constants (house convention, never enum.Enum)."""
from __future__ import annotations

MSI_COUNTERFACTUAL_REPLAY_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# --- Explored-path labels. Phase 1 supports exactly one of each. ----------
PATH_LABEL_BASELINE = "BASELINE"        # what Production actually, really decided (from Series 99).
PATH_LABEL_ALTERNATIVE = "ALTERNATIVE"  # the one real, causal, production-valid alternative Phase 1 explores.

ALL_PATH_LABELS = (PATH_LABEL_BASELINE, PATH_LABEL_ALTERNATIVE)

# --- Replay legality verdict. -----------------------------------------------
LEGALITY_LEGAL = "LEGAL"
LEGALITY_ILLEGAL = "ILLEGAL"

ALL_LEGALITY_STATES = (LEGALITY_LEGAL, LEGALITY_ILLEGAL)

# Phase 1's own, disclosed scope limit -- exactly two explored paths, no
# exhaustive search. A future phase may raise this; Phase 1 hard-codes it
# because the mission's own Replay Modes section does.
PHASE1_MAX_EXPLORED_PATHS = 2
