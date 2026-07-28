"""Opportunity Assessment Engine (OAE) taxonomy — Series 104. Plain
string constants (house convention, never enum.Enum)."""
from __future__ import annotations

MSI_OPPORTUNITY_ASSESSMENT_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# --- The five, exhaustive, mutually-exclusive classifications. Every
# real trading day receives EXACTLY one. ------------------------------------
CLASSIFICATION_GOOD_TRADE_TAKEN = "GOOD_TRADE_TAKEN"
CLASSIFICATION_TRADE_SUBOPTIMAL = "TRADE_SUBOPTIMAL"
CLASSIFICATION_CORRECT_STAY_OUT = "CORRECT_STAY_OUT"
CLASSIFICATION_OPPORTUNITY_IDENTIFIED = "OPPORTUNITY_IDENTIFIED"
CLASSIFICATION_TRADE_SHOULD_NOT_HAVE_OCCURRED = "TRADE_SHOULD_NOT_HAVE_OCCURRED"

ALL_CLASSIFICATIONS = (
    CLASSIFICATION_GOOD_TRADE_TAKEN, CLASSIFICATION_TRADE_SUBOPTIMAL, CLASSIFICATION_CORRECT_STAY_OUT,
    CLASSIFICATION_OPPORTUNITY_IDENTIFIED, CLASSIFICATION_TRADE_SHOULD_NOT_HAVE_OCCURRED,
)

# --- Real, plain-string production decision-outcome values this package
# expects on its DecisionView translation (reused verbatim from
# bujji.msi_decision_auditor.taxonomy -- not re-derived, not imported,
# per this package's own zero-sibling-import isolation, see engine.py). --
DECISION_OUTCOME_TRADE_APPROVED = "TRADE_APPROVED"
DECISION_OUTCOME_NO_TRADE = "NO_TRADE"

# --- Counterfactual legality values, reused verbatim from
# bujji.msi_counterfactual_replay.taxonomy (same non-import discipline). --
REPLAY_LEGALITY_LEGAL = "LEGAL"
REPLAY_LEGALITY_ILLEGAL = "ILLEGAL"

# --- Confidence / evidence strength. Same NONE/LOW/MODERATE/HIGH scale
# used project-wide. -------------------------------------------------------
CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"
ALL_CONFIDENCE_LEVELS = (CONFIDENCE_NONE, CONFIDENCE_LOW, CONFIDENCE_MODERATE, CONFIDENCE_HIGH)
