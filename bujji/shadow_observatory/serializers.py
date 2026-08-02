"""Event -> Artifact serialization/classification -- BUJJI Options OS
v3, Gate V.0.

PURPOSE: turn a real `bujji.core.event_bus.Event` (already plain-
primitive by construction across every F.0-F.5 publish call site --
verified in Step 1 inspection) into the correct artifact record and
destination filename. No interpretation, no threshold, no "if loss >
X" logic of any kind -- purely a lookup table from (EventType, stage
label) to filename, built directly from the REAL stage labels each
module already publishes (documented per-module below).

STEP 1 EVENT INVENTORY (exact stage labels found in each producer):
  F.0 runtime_state_machine.py   -> EventType.STATE_CHANGED, payload
      {from_state, to_state, reason} (no "stage" key).
  F.1 trading_brain_runtime.py   -> SIGNAL_GENERATED (stage=
      STRATEGY_PROPOSED|STRATEGY_REJECTED), DECISION_MADE (stage=
      CONTEXT_UNAVAILABLE|RISK_DECISION|ORDER_SUBMITTED|ORDER_FILLED),
      POSITION_OPENED (stage=POSITION_OPENED).
  F.2 paper.py                    -> DECISION_MADE, stage=
      f"ORDER_LIFECYCLE_{stage}" (CREATED/SUBMITTED/ACCEPTED/
      PARTIALLY_FILLED/FILLED/REJECTED/CANCELLED).
  F.3 portfolio_reality_engine.py -> the ONE new, minimal event this
      gate adds (see module docstring there): HEALTH_CHANGED, stage=
      POSITION_VALUATION_UPDATED -- absolutely necessary per Step 1's
      own finding that nothing else ever publishes MTM/valuation data.
  F.4 trade_lifecycle_executor.py -> DECISION_MADE, stage=
      LIFECYCLE_ACTION_STARTED|LIFECYCLE_ORDER_CREATED|
      LIFECYCLE_ORDER_FILLED|LIFECYCLE_ACTION_COMPLETED|
      LIFECYCLE_ACTION_FAILED.
  F.3 position_lifecycle_runtime.py (position-level lifecycle, NOT
      session state) -> STATE_CHANGED, stage=
      f"POSITION_LIFECYCLE_{state}" -- disambiguated from F.0's own
      STATE_CHANGED (which carries no "stage" key at all) by checking
      for that prefix.
  F.5 shadow_session_controller.py -> publishes nothing of its own;
      it only calls the above, whose events are already captured.
"""
from __future__ import annotations

from typing import Optional, Tuple

from bujji.core.event_bus import Event, EventType

from .models import (
    DecisionArtifact, ErrorArtifact, ExecutionArtifact, HeartbeatArtifact, LifecycleArtifact,
    OrderArtifact, PositionArtifact, StateChangeArtifact,
)

_DECISION_STAGES = ("STRATEGY_PROPOSED", "STRATEGY_REJECTED", "CONTEXT_UNAVAILABLE", "RISK_DECISION")
_ORDER_STAGES = ("ORDER_SUBMITTED", "LIFECYCLE_ORDER_CREATED")
_EXECUTION_STAGES = ("ORDER_FILLED", "LIFECYCLE_ORDER_FILLED")
_POSITION_STAGES = ("POSITION_OPENED", "POSITION_VALUATION_UPDATED")
_LIFECYCLE_ACTION_PREFIX = "LIFECYCLE_ACTION_"
_ORDER_LIFECYCLE_PREFIX = "ORDER_LIFECYCLE_"
_POSITION_LIFECYCLE_PREFIX = "POSITION_LIFECYCLE_"


def classify_destinations(event: Event) -> Tuple[str, ...]:
    """Returns one or more destination filenames -- almost always
    exactly one; an ERROR-bound STATE_CHANGED is deliberately written
    to BOTH state_changes.jsonl (it IS a state transition) AND
    errors.jsonl (it also represents a runtime fault), since both
    are true simultaneously, not a classification ambiguity."""
    stage = event.payload.get("stage", "")

    if event.type == EventType.STATE_CHANGED:
        if stage.startswith(_POSITION_LIFECYCLE_PREFIX):
            return ("lifecycle.jsonl",)
        if event.payload.get("to_state") == "ERROR":
            return ("state_changes.jsonl", "errors.jsonl")
        return ("state_changes.jsonl",)

    if stage in _DECISION_STAGES:
        return ("decisions.jsonl",)
    if stage in _ORDER_STAGES:
        return ("orders.jsonl",)
    if stage in _EXECUTION_STAGES or stage.startswith(_ORDER_LIFECYCLE_PREFIX):
        return ("executions.jsonl",)
    if stage in _POSITION_STAGES:
        return ("positions.jsonl",)
    if stage.startswith(_LIFECYCLE_ACTION_PREFIX):
        return ("lifecycle.jsonl",)

    # Never silently dropped -- an unrecognized stage still lands
    # somewhere observable, in decisions.jsonl as a catch-all, rather
    # than vanishing.
    return ("decisions.jsonl",)


def build_artifact(event: Event, destination: str):
    """Pure conversion -- no interpretation of the event's meaning,
    only reshaping its already-plain payload into the matching
    artifact dataclass."""
    ts = event.timestamp.isoformat()
    payload = event.payload

    if destination == "state_changes.jsonl":
        return StateChangeArtifact(
            timestamp=ts, from_state=payload.get("from_state"), to_state=payload.get("to_state"),
            reason=payload.get("reason"),
        )
    if destination == "errors.jsonl":
        return ErrorArtifact(
            timestamp=ts, exception_type="RUNTIME_STATE_ERROR", module="runtime_state_machine",
            runtime_state=payload.get("to_state"), recovery_action="none_recorded_yet",
            detail=str(payload),
        )
    if destination == "decisions.jsonl":
        return DecisionArtifact(
            timestamp=ts, decision_owner=_infer_decision_owner(payload.get("stage", "")),
            output_decision=payload.get("stage", event.type.value), explanation=dict(payload),
            trace_id=payload.get("assessment_id", payload.get("position_group_id", "")),
        )
    if destination == "orders.jsonl":
        return OrderArtifact(
            timestamp=ts, stage=payload.get("stage", event.type.value),
            position_group_id=payload.get("position_group_id") or payload.get("assessment_id"),
            detail=dict(payload),
        )
    if destination == "executions.jsonl":
        return ExecutionArtifact(timestamp=ts, stage=payload.get("stage", event.type.value), detail=dict(payload))
    if destination == "positions.jsonl":
        return PositionArtifact(
            timestamp=ts, stage=payload.get("stage", event.type.value),
            position_group_id=payload.get("position_group_id") or payload.get("assessment_id"),
            detail=dict(payload),
        )
    if destination == "lifecycle.jsonl":
        return LifecycleArtifact(
            timestamp=ts, stage=payload.get("stage", event.type.value),
            position_group_id=payload.get("position_group_id"), detail=dict(payload),
        )
    raise ValueError(f"unrecognized artifact destination {destination!r}")


def _infer_decision_owner(stage: str) -> str:
    if stage in ("STRATEGY_PROPOSED", "STRATEGY_REJECTED"):
        return "STRATEGY_ENGINE"
    if stage in ("RISK_DECISION", "CONTEXT_UNAVAILABLE"):
        return "RISK_GOVERNOR"
    return "UNKNOWN"
