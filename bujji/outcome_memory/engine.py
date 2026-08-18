"""Outcome Memory Engine -- Phase 15N. Pure functions, no state, no IO,
no broker, no execution, no strategy-selection/decision-synthesis/
trade-intent import of any kind (Step 7's hard boundary).

`build_outcome_memory_record` is the ONLY way a record is created --
it requires a REAL, already-READY `PositionOutcomeAttribution` (Phase
15J); a NOT_READY attribution produces no record at all (an open
position has no outcome yet -- memorizing it would be premature, not
merely incomplete). This module never computes P&L, never re-derives
attribution, never talks to a broker -- it packages already-canonical
records into one immutable historical fact.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from .models import (
    EVENT_OUTCOME_MEMORY_RECORDED, STATUS_KNOWN, STATUS_NOT_APPLICABLE, STATUS_NOT_AVAILABLE, STATUS_UNKNOWN,
    TRANSITION_ACCEPTED, TRANSITION_IDEMPOTENT, TRANSITION_REJECTED,
    OutcomeMemoryRecord, TransitionResult, memory_id_for,
)

_READY = "READY"
_PNL_COMPLETE = "COMPLETE"


def build_outcome_memory_record(lifecycle, attribution, recorded_at: str,
                                 portfolio_snapshot: Optional[object] = None) -> Optional[OutcomeMemoryRecord]:
    """`lifecycle`: a real `PositionLifecycle` (Phase 15G-15K).
    `attribution`: a real `PositionOutcomeAttribution` (Phase 15J).
    `portfolio_snapshot`: OPTIONAL, a real `PortfolioSnapshot` (Phase
    15M) captured around the same time -- never fabricated; omitted
    entirely (`None`) if the caller has no real one to supply, which is
    the honest, common case (Step 12's own finding, see report).
    Returns `None` if `attribution.readiness != READY` -- an
    unattributable position is never memorized."""
    if attribution.readiness != _READY:
        return None

    memory_id = memory_id_for(lifecycle.session_id, lifecycle.position_id, attribution.evaluation_timestamp)

    structured_exit = lifecycle.structured_exit
    if structured_exit is None:
        pnl_status = STATUS_UNKNOWN
    elif structured_exit.get("pnl_status") == _PNL_COMPLETE:
        pnl_status = STATUS_KNOWN
    else:
        pnl_status = STATUS_UNKNOWN  # PARTIAL or UNKNOWN structured-exit resolution both stay honestly UNKNOWN at the memory-query level.

    entry = lifecycle.entry
    greeks_status = STATUS_KNOWN if entry.entry_greeks is not None else STATUS_UNKNOWN
    premium_behaviour_status = STATUS_KNOWN if entry.entry_premium_behaviour is not None else STATUS_UNKNOWN
    management_status = STATUS_KNOWN if lifecycle.management_assessments else STATUS_NOT_APPLICABLE
    portfolio_context_status = STATUS_KNOWN if portfolio_snapshot is not None else STATUS_NOT_AVAILABLE

    return OutcomeMemoryRecord(
        memory_id=memory_id, session_id=lifecycle.session_id, position_id=lifecycle.position_id,
        candidate_id=entry.candidate_id, strategy_family=entry.strategy_family,
        entry_timestamp=lifecycle.opened_at, exit_timestamp=lifecycle.closed_at, recorded_at=recorded_at,
        entry_regime=entry.entry_regime, entry_direction=entry.entry_direction,
        underlying_symbol=entry.underlying_symbol,
        outcome_direction=attribution.outcome_direction, realized_pnl=attribution.realized_pnl,
        pnl_status=pnl_status, final_thesis_status=lifecycle.final_thesis_status,
        primary_cause=attribution.primary_cause,
        management_status=management_status, greeks_status=greeks_status,
        premium_behaviour_status=premium_behaviour_status, portfolio_context_status=portfolio_context_status,
        lifecycle_snapshot=lifecycle.to_dict(), attribution_snapshot=attribution.to_dict(),
        portfolio_context_snapshot=(portfolio_snapshot.to_dict() if portfolio_snapshot is not None else None),
        # getattr, not direct access: at least one real caller
        # (bujji.shadow_lifecycle.orchestrator) supplies a duck-typed
        # attribution view, not always the real PositionOutcomeAttribution
        # dataclass -- confirmed by a full-regression failure caught
        # during this exact change, fixed here rather than assuming
        # every caller's shape matches the dataclass's own.
        mfe=getattr(attribution, "mfe", None), mae=getattr(attribution, "mae", None),
    )


def build_outcome_memory_recorded_payload(record: OutcomeMemoryRecord) -> dict:
    return {"memory_id": record.memory_id, "record": record.to_dict()}


def apply_event(
    states: Dict[str, OutcomeMemoryRecord], event_type: str, payload: dict,
) -> Tuple[Dict[str, OutcomeMemoryRecord], TransitionResult]:
    """The single reducer every live recording call AND every replay/
    hydration call goes through. UNLIKE `position_lifecycle.apply_event`,
    this reducer takes NO target `session_id` -- Outcome Memory is
    deliberately CROSS-SESSION (see models.py's own module docstring);
    every well-formed event is accepted regardless of which session
    produced it. Memory records are IMMUTABLE historical facts: a
    second event for the same `memory_id` with IDENTICAL content is a
    genuine retry (IDEMPOTENT); with DIFFERENT content it is a
    conflicting event and REJECTED outright -- a memory record is
    never silently overwritten."""
    if event_type != EVENT_OUTCOME_MEMORY_RECORDED:
        return states, TransitionResult(TRANSITION_REJECTED, event_type, None, f"unknown event type {event_type!r}")

    memory_id = payload.get("memory_id")
    record_dict = payload.get("record")
    if not memory_id or not isinstance(record_dict, dict):
        return states, TransitionResult(TRANSITION_REJECTED, event_type, memory_id, "malformed payload: missing memory_id/record")

    try:
        record = OutcomeMemoryRecord.from_dict(record_dict)
    except (KeyError, TypeError) as exc:
        return states, TransitionResult(TRANSITION_REJECTED, event_type, memory_id, f"malformed record: {exc}")

    if memory_id in states:
        existing = states[memory_id]
        if existing.to_dict() == record.to_dict():
            return states, TransitionResult(TRANSITION_IDEMPOTENT, event_type, memory_id,
                                             "duplicate OUTCOME_MEMORY_RECORDED with identical content -- a genuine retry, treated as a no-op")
        return states, TransitionResult(TRANSITION_REJECTED, event_type, memory_id,
                                         "memory_id already exists with DIFFERENT content -- a conflicting event, "
                                         "rejected; historical memory is immutable and never silently overwritten")

    new_states = dict(states)
    new_states[memory_id] = record
    return new_states, TransitionResult(TRANSITION_ACCEPTED, event_type, memory_id, "outcome memory recorded")
