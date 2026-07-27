"""Execution Engine vocabulary — BUJJI Options OS v3, Engineering
Series 39, Sprint 1.

Three finite vocabularies belong to this module: `status`,
`abstract_action`, and `blocking_condition`. Nothing here is a broker
instruction, an order field, a REST payload, or a contract quantity.
One internal-only ordinal ordering (never exposed as public
vocabulary) exists purely so engine.py can step confidence down by one
level -- the same disclosed, calculation-only-ordinal pattern used
throughout the Trading Brain since the Market State Builder (Series
33).
"""
from __future__ import annotations

EXECUTION_ENGINE_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Status -- the overall verdict.
# ---------------------------------------------------------------------------
STATUS_VERSION = "1.0.0"

STATUS_UNKNOWN = "UNKNOWN"
STATUS_BLOCKED = "BLOCKED"
STATUS_READY_FOR_ADAPTER = "READY_FOR_ADAPTER"

ALL_STATUSES = (
    STATUS_UNKNOWN,
    STATUS_BLOCKED,
    STATUS_READY_FOR_ADAPTER,
)

STATUS_DESCRIPTIONS = {
    STATUS_UNKNOWN: "Execution status could not be determined.",
    STATUS_BLOCKED: "The plan cannot proceed to a Broker Adapter.",
    STATUS_READY_FOR_ADAPTER: "An ordered, validated instruction set is ready for a future Broker Adapter to consume.",
}

# ---------------------------------------------------------------------------
# Abstract Action -- finite, closed workflow vocabulary. Never a broker
# instruction, an order field, or a payload.
# ---------------------------------------------------------------------------
ABSTRACT_ACTION_VERSION = "1.0.0"

ACTION_VALIDATE_PLAN = "VALIDATE_PLAN"
ACTION_VALIDATE_CONTROLS = "VALIDATE_CONTROLS"
ACTION_AUTHORIZE_EXECUTION = "AUTHORIZE_EXECUTION"
ACTION_WAIT_FOR_ADAPTER = "WAIT_FOR_ADAPTER"
ACTION_EXECUTE = "EXECUTE"
ACTION_COMPLETE = "COMPLETE"
ACTION_BLOCK = "BLOCK"

ALL_ABSTRACT_ACTIONS = (
    ACTION_VALIDATE_PLAN,
    ACTION_VALIDATE_CONTROLS,
    ACTION_AUTHORIZE_EXECUTION,
    ACTION_WAIT_FOR_ADAPTER,
    ACTION_EXECUTE,
    ACTION_COMPLETE,
    ACTION_BLOCK,
)

ABSTRACT_ACTION_DESCRIPTIONS = {
    ACTION_VALIDATE_PLAN: "Confirm the ExecutionPlan itself is well-formed and names a strategy.",
    ACTION_VALIDATE_CONTROLS: "Confirm the required controls carried on the plan are present and recognized.",
    ACTION_AUTHORIZE_EXECUTION: "Confirm this instruction set may be handed off for execution.",
    ACTION_WAIT_FOR_ADAPTER: "Hand off to, and await, a future Broker Adapter.",
    ACTION_EXECUTE: "Carry out an order. Never performed by this engine.",
    ACTION_COMPLETE: "Mark a position's execution lifecycle as finished. Never performed by this engine.",
    ACTION_BLOCK: "Halt the workflow; no adapter hand-off occurs.",
}

# `EXECUTE` and `COMPLETE` are declared for taxonomy completeness and
# forward compatibility but are never produced by this sprint's engine
# -- this module orchestrates up to `WAIT_FOR_ADAPTER` and stops. It
# never executes, and never observes an execution's completion; both
# require a live Broker Adapter this module deliberately does not
# have.
UNREACHABLE_ACTIONS_THIS_SPRINT = (ACTION_EXECUTE, ACTION_COMPLETE)

# ---------------------------------------------------------------------------
# Blocking Condition -- finite, closed. Never free text.
# ---------------------------------------------------------------------------
BLOCKING_CONDITION_VERSION = "1.0.0"

BLOCKING_CONDITION_MISSING_PLAN = "MISSING_PLAN"
BLOCKING_CONDITION_FAILED_VALIDATION = "FAILED_VALIDATION"
BLOCKING_CONDITION_CONTROL_FAILURE = "CONTROL_FAILURE"
BLOCKING_CONDITION_UNKNOWN_INTENT = "UNKNOWN_INTENT"

ALL_BLOCKING_CONDITIONS = (
    BLOCKING_CONDITION_MISSING_PLAN,
    BLOCKING_CONDITION_FAILED_VALIDATION,
    BLOCKING_CONDITION_CONTROL_FAILURE,
    BLOCKING_CONDITION_UNKNOWN_INTENT,
)

BLOCKING_CONDITION_DESCRIPTIONS = {
    BLOCKING_CONDITION_MISSING_PLAN: "No usable ExecutionPlan was supplied, or its own status is itself UNKNOWN.",
    BLOCKING_CONDITION_FAILED_VALIDATION: "The plan itself was BLOCKED upstream, or failed this engine's own structural validation.",
    BLOCKING_CONDITION_CONTROL_FAILURE: "A required control on the plan could not be recognized or verified.",
    BLOCKING_CONDITION_UNKNOWN_INTENT: "The plan carries no actionable execution intent (NOT_PLANNED).",
}

# `CONTROL_FAILURE` is declared for taxonomy completeness and forward
# compatibility. It is reachable only via a defensive check on plans
# this engine does not trust blindly (a PLANNED status naming no
# controls object at all); Execution Planner's own contract never
# actually produces that combination, so this path is structurally
# rare by construction -- see docs/EXECUTION_ENGINE_ARCHITECTURE.md.

# ---------------------------------------------------------------------------
# Internal-only ordinal ordering. Never exposed as a public vocabulary
# of this module's own; owned by the frozen Trading Ontology (Series
# 31) and reused, read-only, by every downstream module since.
# ---------------------------------------------------------------------------
CONFIDENCE_ORDER = ("UNKNOWN", "VERY_LOW", "LOW", "MODERATE", "HIGH", "VERY_HIGH")
