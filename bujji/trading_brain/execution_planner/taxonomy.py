"""Execution Planner vocabulary — BUJJI Options OS v3, Engineering
Series 37, Sprint 1.

Four finite vocabularies belong to this module: `status`,
`execution_intent`, `execution_constraint`, and `execution_step_type`.
Nothing here is a dynamic string, an order field, a broker payload, or
a contract quantity. One internal-only ordinal ordering (never exposed
as public vocabulary) exists purely so engine.py can step confidence
down by one level -- the same disclosed, calculation-only-ordinal
pattern used throughout the Trading Brain since the Market State
Builder (Series 33).
"""
from __future__ import annotations

EXECUTION_PLANNER_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Execution Status -- the overall verdict.
# ---------------------------------------------------------------------------
STATUS_VERSION = "1.0.0"

STATUS_UNKNOWN = "UNKNOWN"
STATUS_NOT_PLANNED = "NOT_PLANNED"
STATUS_PLANNED = "PLANNED"
STATUS_BLOCKED = "BLOCKED"

ALL_STATUSES = (
    STATUS_UNKNOWN,
    STATUS_NOT_PLANNED,
    STATUS_PLANNED,
    STATUS_BLOCKED,
)

STATUS_DESCRIPTIONS = {
    STATUS_UNKNOWN: "Planning status could not be determined.",
    STATUS_NOT_PLANNED: "No plan was created because capital allocation was denied.",
    STATUS_PLANNED: "A broker-independent execution plan was created.",
    STATUS_BLOCKED: "Planning could not proceed because a required upstream input was missing or unresolved.",
}

# ---------------------------------------------------------------------------
# Execution Intent -- the conceptual workflow phase this plan targets.
# ---------------------------------------------------------------------------
EXECUTION_INTENT_VERSION = "1.0.0"

EXECUTION_INTENT_NONE = "NONE"
EXECUTION_INTENT_PREPARE = "PREPARE"
EXECUTION_INTENT_ENTER = "ENTER"
EXECUTION_INTENT_MONITOR = "MONITOR"
EXECUTION_INTENT_EXIT = "EXIT"
EXECUTION_INTENT_UNKNOWN = "UNKNOWN"

ALL_EXECUTION_INTENTS = (
    EXECUTION_INTENT_NONE,
    EXECUTION_INTENT_PREPARE,
    EXECUTION_INTENT_ENTER,
    EXECUTION_INTENT_MONITOR,
    EXECUTION_INTENT_EXIT,
    EXECUTION_INTENT_UNKNOWN,
)

EXECUTION_INTENT_DESCRIPTIONS = {
    EXECUTION_INTENT_NONE: "No execution activity is intended.",
    EXECUTION_INTENT_PREPARE: "The plan prepares for a future execution engine to act, but does not itself act.",
    EXECUTION_INTENT_ENTER: "The plan targets opening a new position.",
    EXECUTION_INTENT_MONITOR: "The plan targets monitoring an existing position.",
    EXECUTION_INTENT_EXIT: "The plan targets closing an existing position.",
    EXECUTION_INTENT_UNKNOWN: "Execution intent could not be determined.",
}

# `ENTER`, `MONITOR`, and `EXIT` are declared for taxonomy completeness
# and forward compatibility but are never produced by this sprint's
# policy -- see docs/EXECUTION_PLANNER_ARCHITECTURE.md's
# disclosed-limitation note. This sprint's planner only ever prepares;
# a future Execution Engine, with live position awareness this module
# deliberately does not have, is what actually enters, monitors, or
# exits.
UNREACHABLE_EXECUTION_INTENTS_THIS_SPRINT = (
    EXECUTION_INTENT_ENTER,
    EXECUTION_INTENT_MONITOR,
    EXECUTION_INTENT_EXIT,
)

# ---------------------------------------------------------------------------
# Execution Constraint -- finite, closed. `NONE` is itself a member so
# that "no constraint applies" is always an explicit, named value.
# ---------------------------------------------------------------------------
EXECUTION_CONSTRAINT_VERSION = "1.0.0"

CONSTRAINT_NONE = "NONE"
CONSTRAINT_WAIT_FOR_CONFIRMATION = "WAIT_FOR_CONFIRMATION"
CONSTRAINT_REDUCE_SIZE = "REDUCE_SIZE"
CONSTRAINT_MANUAL_APPROVAL = "MANUAL_APPROVAL"
CONSTRAINT_FOLLOW_RISK_CONTROLS = "FOLLOW_RISK_CONTROLS"

ALL_EXECUTION_CONSTRAINTS = (
    CONSTRAINT_NONE,
    CONSTRAINT_WAIT_FOR_CONFIRMATION,
    CONSTRAINT_REDUCE_SIZE,
    CONSTRAINT_MANUAL_APPROVAL,
    CONSTRAINT_FOLLOW_RISK_CONTROLS,
)

EXECUTION_CONSTRAINT_DESCRIPTIONS = {
    CONSTRAINT_NONE: "No constraint applies.",
    CONSTRAINT_WAIT_FOR_CONFIRMATION: "A future Execution Engine should wait for an external confirmation before acting.",
    CONSTRAINT_REDUCE_SIZE: "A future Execution Engine should size any resulting order below its default, per the Capital Brain's own reduced allocation.",
    CONSTRAINT_MANUAL_APPROVAL: "A human must approve this plan before any execution engine acts on it.",
    CONSTRAINT_FOLLOW_RISK_CONTROLS: "Whatever controls the Risk Brain and Capital Brain already named must be followed.",
}

# `WAIT_FOR_CONFIRMATION` is declared for taxonomy completeness and
# forward compatibility but is never produced by this sprint's policy
# -- this module has no signal from CapitalDecision/StrategyDecision
# alone that specifically calls for an external confirmation wait
# rather than the broader MANUAL_APPROVAL or FOLLOW_RISK_CONTROLS
# constraints it already expresses.
UNREACHABLE_EXECUTION_CONSTRAINTS_THIS_SPRINT = (CONSTRAINT_WAIT_FOR_CONFIRMATION,)

# ---------------------------------------------------------------------------
# Execution Step Type -- finite, closed. Describes conceptual workflow
# steps only -- never a broker instruction, order field, or payload.
# ---------------------------------------------------------------------------
EXECUTION_STEP_TYPE_VERSION = "1.0.0"

STEP_TYPE_VALIDATE = "VALIDATE"
STEP_TYPE_PREPARE = "PREPARE"
STEP_TYPE_WAIT = "WAIT"
STEP_TYPE_ENTER = "ENTER"
STEP_TYPE_MONITOR = "MONITOR"
STEP_TYPE_EXIT = "EXIT"

ALL_EXECUTION_STEP_TYPES = (
    STEP_TYPE_VALIDATE,
    STEP_TYPE_PREPARE,
    STEP_TYPE_WAIT,
    STEP_TYPE_ENTER,
    STEP_TYPE_MONITOR,
    STEP_TYPE_EXIT,
)

EXECUTION_STEP_TYPE_DESCRIPTIONS = {
    STEP_TYPE_VALIDATE: "Confirm a prerequisite is present and consistent.",
    STEP_TYPE_PREPARE: "Prepare for a future execution engine to act.",
    STEP_TYPE_WAIT: "Hand off to, and await, a future execution engine.",
    STEP_TYPE_ENTER: "Open a new position.",
    STEP_TYPE_MONITOR: "Monitor an existing position.",
    STEP_TYPE_EXIT: "Close an existing position.",
}

# `ENTER`, `MONITOR`, `EXIT` step types are likewise declared for
# completeness but never appear in a step this sprint's engine
# produces, for the same reason the execution intents of the same
# names are unreachable.
UNREACHABLE_STEP_TYPES_THIS_SPRINT = (STEP_TYPE_ENTER, STEP_TYPE_MONITOR, STEP_TYPE_EXIT)

# ---------------------------------------------------------------------------
# Internal-only ordinal ordering. Never exposed as a public vocabulary
# of this module's own; owned by the frozen Trading Ontology (Series
# 31) and reused, read-only, by every downstream module since.
# ---------------------------------------------------------------------------
CONFIDENCE_ORDER = ("UNKNOWN", "VERY_LOW", "LOW", "MODERATE", "HIGH", "VERY_HIGH")
