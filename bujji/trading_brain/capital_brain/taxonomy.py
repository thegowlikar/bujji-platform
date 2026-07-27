"""Capital Brain vocabulary — BUJJI Options OS v3, Engineering Series
36, Sprint 1.

Four finite vocabularies belong to this module: `capital_intent`,
`allocation_status`, `constraint`, and `required_control`. Nothing
here is a dynamic string, a number, a lot count, or a margin figure.
One internal-only ordinal ordering (never exposed as public
vocabulary) exists purely so engine.py can step confidence down by one
level -- the same disclosed, calculation-only-ordinal pattern used
throughout the Trading Brain since the Market State Builder (Series
33).
"""
from __future__ import annotations

CAPITAL_BRAIN_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Capital Intent -- the authorized capital policy.
# ---------------------------------------------------------------------------
CAPITAL_INTENT_VERSION = "1.0.0"

CAPITAL_INTENT_UNKNOWN = "UNKNOWN"
CAPITAL_INTENT_NONE = "NONE"
CAPITAL_INTENT_MINIMAL = "MINIMAL"
CAPITAL_INTENT_REDUCED = "REDUCED"
CAPITAL_INTENT_STANDARD = "STANDARD"
CAPITAL_INTENT_FULL = "FULL"

ALL_CAPITAL_INTENTS = (
    CAPITAL_INTENT_UNKNOWN,
    CAPITAL_INTENT_NONE,
    CAPITAL_INTENT_MINIMAL,
    CAPITAL_INTENT_REDUCED,
    CAPITAL_INTENT_STANDARD,
    CAPITAL_INTENT_FULL,
)

CAPITAL_INTENT_DESCRIPTIONS = {
    CAPITAL_INTENT_UNKNOWN: "Capital intent could not be determined.",
    CAPITAL_INTENT_NONE: "No capital should be committed.",
    CAPITAL_INTENT_MINIMAL: "Only a minimal amount of capital should be committed.",
    CAPITAL_INTENT_REDUCED: "A below-standard amount of capital should be committed.",
    CAPITAL_INTENT_STANDARD: "The standard amount of capital should be committed.",
    CAPITAL_INTENT_FULL: "The full amount of capital should be committed.",
}

# `FULL` is declared for taxonomy completeness and forward
# compatibility but is never reached by this sprint's policy -- see
# docs/CAPITAL_BRAIN_ARCHITECTURE.md's disclosed-limitation note.
UNREACHABLE_CAPITAL_INTENTS_THIS_SPRINT = (CAPITAL_INTENT_FULL,)

# ---------------------------------------------------------------------------
# Allocation Status -- the overall verdict.
# ---------------------------------------------------------------------------
ALLOCATION_STATUS_VERSION = "1.0.0"

ALLOCATION_STATUS_UNKNOWN = "UNKNOWN"
ALLOCATION_STATUS_DENIED = "DENIED"
ALLOCATION_STATUS_LIMITED = "LIMITED"
ALLOCATION_STATUS_APPROVED = "APPROVED"

ALL_ALLOCATION_STATUSES = (
    ALLOCATION_STATUS_UNKNOWN,
    ALLOCATION_STATUS_DENIED,
    ALLOCATION_STATUS_LIMITED,
    ALLOCATION_STATUS_APPROVED,
)

ALLOCATION_STATUS_DESCRIPTIONS = {
    ALLOCATION_STATUS_UNKNOWN: "Allocation status could not be determined.",
    ALLOCATION_STATUS_DENIED: "No capital allocation is authorized today.",
    ALLOCATION_STATUS_LIMITED: "Capital allocation is authorized only under named constraints.",
    ALLOCATION_STATUS_APPROVED: "Capital allocation is authorized with no additional constraints.",
}

# ---------------------------------------------------------------------------
# Constraint -- finite, closed. `NONE` is itself a member so that "no
# constraint applies" is always an explicit, named value rather than
# an ambiguous empty collection.
# ---------------------------------------------------------------------------
CONSTRAINT_VERSION = "1.0.0"

CONSTRAINT_NONE = "NONE"
CONSTRAINT_REDUCE_EXPOSURE = "REDUCE_EXPOSURE"
CONSTRAINT_MAX_SINGLE_POSITION = "MAX_SINGLE_POSITION"
CONSTRAINT_REQUIRE_HEDGE = "REQUIRE_HEDGE"
CONSTRAINT_MANUAL_REVIEW = "MANUAL_REVIEW"

ALL_CONSTRAINTS = (
    CONSTRAINT_NONE,
    CONSTRAINT_REDUCE_EXPOSURE,
    CONSTRAINT_MAX_SINGLE_POSITION,
    CONSTRAINT_REQUIRE_HEDGE,
    CONSTRAINT_MANUAL_REVIEW,
)

CONSTRAINT_DESCRIPTIONS = {
    CONSTRAINT_NONE: "No constraint applies.",
    CONSTRAINT_REDUCE_EXPOSURE: "A future Execution Planner should reduce total exposure versus its default.",
    CONSTRAINT_MAX_SINGLE_POSITION: "A future Execution Planner should cap any single position below its default maximum.",
    CONSTRAINT_REQUIRE_HEDGE: "A future Execution Planner should require an accompanying hedge.",
    CONSTRAINT_MANUAL_REVIEW: "A human must review this decision before any capital is committed.",
}

# `REQUIRE_HEDGE` is declared for taxonomy completeness and forward
# compatibility but is never reached by this sprint's policy -- this
# module has no signal from RiskAssessment alone that specifically
# calls for a hedge rather than a broader exposure reduction.
UNREACHABLE_CONSTRAINTS_THIS_SPRINT = (CONSTRAINT_REQUIRE_HEDGE,)

# ---------------------------------------------------------------------------
# Required Control -- finite, closed, three values only (per spec).
# ---------------------------------------------------------------------------
REQUIRED_CONTROL_VERSION = "1.0.0"

REQUIRED_CONTROL_NONE = "NONE"
REQUIRED_CONTROL_FOLLOW_RISK_CONTROLS = "FOLLOW_RISK_CONTROLS"
REQUIRED_CONTROL_FOLLOW_LIMITS = "FOLLOW_LIMITS"

ALL_REQUIRED_CONTROLS = (
    REQUIRED_CONTROL_NONE,
    REQUIRED_CONTROL_FOLLOW_RISK_CONTROLS,
    REQUIRED_CONTROL_FOLLOW_LIMITS,
)

REQUIRED_CONTROL_DESCRIPTIONS = {
    REQUIRED_CONTROL_NONE: "No required control applies.",
    REQUIRED_CONTROL_FOLLOW_RISK_CONTROLS: "Whatever controls the Risk Brain already named must be followed.",
    REQUIRED_CONTROL_FOLLOW_LIMITS: "The capital limits implied by this decision's constraints must be followed.",
}

# ---------------------------------------------------------------------------
# Internal-only ordinal ordering. Never exposed as a public vocabulary
# of this module's own; owned by the frozen Trading Ontology (Series
# 31) and reused, read-only, by every downstream module since.
# ---------------------------------------------------------------------------
CONFIDENCE_ORDER = ("UNKNOWN", "VERY_LOW", "LOW", "MODERATE", "HIGH", "VERY_HIGH")
