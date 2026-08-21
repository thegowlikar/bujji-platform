"""Engineering Evidence Board (EEB) taxonomy — Series 106. Plain string
constants (house convention, never enum.Enum)."""
from __future__ import annotations

MSI_ENGINEERING_EVIDENCE_BOARD_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# --- The five, exhaustive board decisions. Every real report receives
# EXACTLY one. Only three (INSUFFICIENT_EVIDENCE/CONTINUE_OBSERVING/
# READY_FOR_ENGINEERING_REVIEW) are ever computed by review_evidence();
# ARCHIVED is only ever produced by the explicit, human-initiated
# archive() function; SUPERSEDED is only ever a query-time derived view
# over journal history (see query.py) -- never a value engine.py assigns
# to a freshly-built report. -------------------------------------------
DECISION_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
DECISION_CONTINUE_OBSERVING = "CONTINUE_OBSERVING"
DECISION_READY_FOR_ENGINEERING_REVIEW = "READY_FOR_ENGINEERING_REVIEW"
DECISION_SUPERSEDED = "SUPERSEDED"
DECISION_ARCHIVED = "ARCHIVED"

ALL_DECISIONS = (
    DECISION_INSUFFICIENT_EVIDENCE, DECISION_CONTINUE_OBSERVING, DECISION_READY_FOR_ENGINEERING_REVIEW,
    DECISION_SUPERSEDED, DECISION_ARCHIVED,
)

# --- Real, plain-string validation-state values this package expects on
# its KnowledgeValidationView (reused verbatim from
# bujji.msi_knowledge_validation.taxonomy -- not imported, per this
# package's own zero-sibling-import isolation, see engine.py). -----------
VALIDATION_STATE_NOT_OBSERVED = "NOT_OBSERVED"
VALIDATION_STATE_OBSERVED = "OBSERVED"
VALIDATION_STATE_REPEATED = "REPEATED"
VALIDATION_STATE_EMERGING = "EMERGING"
VALIDATION_STATE_VALIDATED = "VALIDATED"
VALIDATION_STATE_DECAYING = "DECAYING"
VALIDATION_STATE_INVALIDATED = "INVALIDATED"

# --- Real OAE classifications this package treats as "positive" evidence
# of genuine opportunity quality (reused verbatim from
# bujji.msi_opportunity_assessment.taxonomy -- not imported). -------------
OPPORTUNITY_POSITIVE_CLASSIFICATIONS = ("OPPORTUNITY_IDENTIFIED", "GOOD_TRADE_TAKEN")
