"""Position Lifecycle Engine -- Phase 15G. Pure functions, no state, no
IO, no broker, no execution. Builds/persists PositionOpened/
ThesisEvaluated/PositionClosed PAYLOADS (plain dicts, ready to hand to
`bujji.state_persistence.EventStore.append`) and replays them via a
pure reducer (`apply_event`) -- the SAME function both a live session
and a hydration/replay call use, so replay can never silently diverge
from live behavior (same discipline as every Phase 15B-15F reducer).

Ownership boundary: this module NEVER calls a broker, NEVER computes
P&L, NEVER decides to open or close a position -- it only records that
a caller-supplied, already-real decision happened, and replays that
record deterministically. `close_position`/`open_position` build event
PAYLOADS; nothing here appends to a store or talks to PaperBroker.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from .identity import leg_id_for, position_id_for
from .models import (
    EVENT_MANAGEMENT_ASSESSED, EVENT_OUTCOME_ATTRIBUTED, EVENT_POSITION_CLOSED, EVENT_POSITION_OPENED,
    EVENT_THESIS_EVALUATED, PNL_COMPLETE, PNL_PARTIAL, PNL_UNKNOWN,
    STATUS_CLOSED, STATUS_OPEN, TRANSITION_ACCEPTED, TRANSITION_IDEMPOTENT, TRANSITION_REJECTED,
    EntrySnapshot, ExitLegRecord, LegRecord, PositionLifecycle, StructuredExit, TransitionResult,
)
from .pnl import compute_leg_gross_pnl, compute_net_pnl, compute_position_gross_pnl


def build_entry_snapshot_for_position(candidate, source_intelligence_cycle_record: Optional[dict]) -> EntrySnapshot:
    """`candidate`: a real ShadowTradeCandidate (Phase 14).
    `source_intelligence_cycle_record`: the SAME real intelligence_cycle
    record the candidate was constructed from -- optional, mirrors
    Position Intelligence's own `build_entry_snapshot` (Phase 15F)
    precedent exactly, including UNKNOWN-safe defaults when omitted."""
    entry_greeks = entry_premium_behaviour = None
    if source_intelligence_cycle_record is not None:
        entry_greeks = source_intelligence_cycle_record.get("greeks")
        entry_premium_behaviour = source_intelligence_cycle_record.get("premium_behaviour")
    return EntrySnapshot(
        candidate_id=candidate.candidate_id, source_cycle_id=candidate.source_cycle_id,
        strategy_family=candidate.strategy_family, entry_timestamp=candidate.timestamp,
        underlying_price=candidate.underlying_price, entry_direction=candidate.direction,
        entry_regime=candidate.market_regime, entry_thesis=candidate.thesis,
        entry_confidence=candidate.selection_confidence,
        entry_greeks=entry_greeks, entry_premium_behaviour=entry_premium_behaviour,
        # Phase 15K: the real contract multiplier, carried forward from
        # ShadowTradeCandidate.lot_size (Phase 14) -- getattr-guarded so
        # a minimal test double without this attribute still works,
        # same defensive style already used throughout this function.
        lot_size=getattr(candidate, "lot_size", None),
        # Phase 15M: the real underlying ticker, carried forward from
        # ShadowTradeCandidate.underlying_symbol (Phase 14) -- needed
        # for portfolio-level concentration-by-underlying.
        underlying_symbol=getattr(candidate, "underlying_symbol", None),
    )


def build_position_opened_payload(session_id: str, candidate, entry: EntrySnapshot, entry_timestamp: str) -> dict:
    """Builds the PositionOpened event payload -- multi-leg correct:
    every leg gets its OWN leg_id, scoped under the position_id, never
    a separate top-level identity. Legs are copied verbatim from the
    candidate's own real, already-solved leg data (Phase 14) -- nothing
    re-derived, nothing fabricated."""
    position_id = position_id_for(session_id, candidate.candidate_id, entry_timestamp)
    legs = tuple(
        LegRecord(
            leg_id=leg_id_for(position_id, leg.role, leg.option_type, leg.strike, leg.expiry),
            role=leg.role, option_type=leg.option_type, strike=leg.strike, expiry=leg.expiry,
            side=leg.side, quantity=leg.ratio, entry_premium=leg.entry_mid, entry_delta=leg.delta,
            # Phase 15K: real bid/ask, already solved on ShadowTradeLeg
            # (Phase 14) but never carried onto LegRecord before now.
            entry_bid=getattr(leg, "entry_bid", None), entry_ask=getattr(leg, "entry_ask", None),
        )
        for leg in candidate.legs
    )
    return {
        "position_id": position_id, "entry": entry.to_dict(),
        "legs": [l.to_dict() for l in legs], "opened_at": entry_timestamp,
    }


def build_thesis_evaluated_payload(position_id: str, cycle_id: str, evaluation) -> dict:
    """`evaluation`: a real ThesisEvaluation (Phase 15F)."""
    return {"position_id": position_id, "cycle_id": cycle_id, "evaluation": evaluation.to_dict()}


def build_position_closed_payload(
    position_id: str, exit_timestamp: str, exit_reason: str, structured_exit: Optional[StructuredExit] = None,
) -> dict:
    """`structured_exit`: Phase 15K additive, optional parameter --
    ENRICHES the SAME `POSITION_CLOSED` event rather than adding a
    second event type (Step 7's forensic decision: an exit and a close
    are the same real-world moment in this system's current scope --
    no partial exits exist -- so splitting them would only double
    persistence and complicate guard logic for no real benefit).
    Omitting it (every pre-Phase-15K call site) produces the EXACT
    SAME payload shape as before, plus one new `"structured_exit":
    None` key -- backward compatible."""
    return {
        "position_id": position_id, "closed_at": exit_timestamp, "exit_reason": exit_reason,
        "structured_exit": structured_exit.to_dict() if structured_exit is not None else None,
    }


def build_structured_exit(
    legs: Tuple[LegRecord, ...], lot_size: Optional[int], exit_timestamp: str, exit_reason: str,
    exit_prices: Dict[str, dict], fees: Optional[float] = None, slippage: Optional[float] = None,
) -> StructuredExit:
    """`exit_prices`: {leg_id: {"exit_price": ..., "exit_bid": ..., "exit_ask": ..., "exit_quantity": ...}} --
    real exit evidence supplied by the caller (never invented here).
    A leg with no entry in `exit_prices` degrades honestly to
    `PNL_UNKNOWN` for that leg -- never assumed to have exited at any
    particular price. Uses `bujji.position_lifecycle.pnl` DIRECTLY,
    the single pure P&L calculation source -- never re-implemented here."""
    exit_legs = []
    leg_pnls = []
    for leg in legs:
        exit_info = exit_prices.get(leg.leg_id) or {}
        exit_price = exit_info.get("exit_price")
        gross_leg_pnl, pnl_status = compute_leg_gross_pnl(leg.side, leg.quantity, lot_size, leg.entry_premium, exit_price)
        net_leg_pnl = None  # per-leg fee/slippage attribution is not represented this phase -- only position-level fees/slippage exist.
        exit_legs.append(ExitLegRecord(
            leg_id=leg.leg_id, exit_price=exit_price, exit_bid=exit_info.get("exit_bid"),
            exit_ask=exit_info.get("exit_ask"), exit_quantity=exit_info.get("exit_quantity", leg.quantity),
            gross_leg_pnl=gross_leg_pnl, fees=None, net_leg_pnl=net_leg_pnl, pnl_status=pnl_status,
        ))
        leg_pnls.append((gross_leg_pnl, pnl_status))

    gross_realized_pnl, position_pnl_status = compute_position_gross_pnl(leg_pnls)
    net_realized_pnl = compute_net_pnl(gross_realized_pnl, fees, slippage)

    return StructuredExit(
        exit_timestamp=exit_timestamp, exit_reason=exit_reason, legs=tuple(exit_legs),
        gross_realized_pnl=gross_realized_pnl, fees=fees, slippage=slippage,
        net_realized_pnl=net_realized_pnl, pnl_status=position_pnl_status,
    )


def build_management_assessed_payload(position_id: str, cycle_id: str, assessment) -> dict:
    """`assessment`: a real PositionManagementAssessment (Phase 15I).
    Recording this event NEVER implies the recommended action
    (adjust/hedge/roll/exit) was actually taken -- it is advisory
    evidence only, same discipline as build_thesis_evaluated_payload."""
    return {"position_id": position_id, "cycle_id": cycle_id, "assessment": assessment.to_dict()}


def build_outcome_attributed_payload(position_id: str, attribution) -> dict:
    """`attribution`: a real PositionOutcomeAttribution (Phase 15J).
    Immutable historical evidence -- recording this NEVER mutates
    status, legs, or any decision field."""
    return {"position_id": position_id, "attribution": attribution.to_dict()}


def _apply_position_opened(states: Dict[str, PositionLifecycle], session_id: str, payload: dict) -> Tuple[Dict[str, PositionLifecycle], TransitionResult]:
    position_id = payload.get("position_id")
    if not position_id:
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_POSITION_OPENED, None, "malformed payload: missing position_id")
    if position_id in states:
        existing = states[position_id]
        same_content = (
            existing.opened_at == payload.get("opened_at")
            and existing.entry.to_dict() == payload.get("entry")
            and [l.to_dict() for l in existing.legs] == payload.get("legs")
        )
        if same_content:
            return states, TransitionResult(TRANSITION_IDEMPOTENT, EVENT_POSITION_OPENED, position_id,
                                             "duplicate POSITION_OPENED for an already-open position with identical "
                                             "content -- a genuine retry, treated as a no-op")
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_POSITION_OPENED, position_id,
                                         "position_id already exists with DIFFERENT content -- a conflicting event, "
                                         "rejected; a position identity can never be silently reused for a different entry")
    try:
        entry = EntrySnapshot.from_dict(payload["entry"])
        legs = tuple(LegRecord.from_dict(l) for l in payload["legs"])
        opened_at = payload["opened_at"]
    except (KeyError, TypeError):
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_POSITION_OPENED, position_id, "malformed payload: missing required field")

    new_lifecycle = PositionLifecycle(
        position_id=position_id, session_id=session_id, status=STATUS_OPEN,
        entry=entry, legs=legs, opened_at=opened_at,
    )
    new_states = dict(states)
    new_states[position_id] = new_lifecycle
    return new_states, TransitionResult(TRANSITION_ACCEPTED, EVENT_POSITION_OPENED, position_id, "position opened")


def _apply_thesis_evaluated(states: Dict[str, PositionLifecycle], payload: dict) -> Tuple[Dict[str, PositionLifecycle], TransitionResult]:
    position_id = payload.get("position_id")
    if not position_id or position_id not in states:
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_THESIS_EVALUATED, position_id,
                                         "unknown position_id -- cannot evaluate a thesis for a position that was never opened")
    current = states[position_id]
    if current.status != STATUS_OPEN:
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_THESIS_EVALUATED, position_id,
                                         f"position is {current.status}, not OPEN -- a closed position cannot receive new thesis evaluations")
    evaluation = payload.get("evaluation")
    if not isinstance(evaluation, dict):
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_THESIS_EVALUATED, position_id, "malformed payload: missing evaluation")

    updated = PositionLifecycle(
        position_id=current.position_id, session_id=current.session_id, status=current.status,
        entry=current.entry, legs=current.legs, opened_at=current.opened_at,
        thesis_evaluations=current.thesis_evaluations + (evaluation,),
        management_assessments=current.management_assessments,
        closed_at=current.closed_at, exit_reason=current.exit_reason,
        final_thesis_status=evaluation.get("thesis_status"),
        latest_management_recommendation=current.latest_management_recommendation,
        realized_pnl=current.realized_pnl,
    )
    new_states = dict(states)
    new_states[position_id] = updated
    return new_states, TransitionResult(TRANSITION_ACCEPTED, EVENT_THESIS_EVALUATED, position_id, "thesis evaluation recorded")


def _apply_management_assessed(states: Dict[str, PositionLifecycle], payload: dict) -> Tuple[Dict[str, PositionLifecycle], TransitionResult]:
    position_id = payload.get("position_id")
    if not position_id or position_id not in states:
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_MANAGEMENT_ASSESSED, position_id,
                                         "unknown position_id -- cannot record a management assessment for a position that was never opened")
    current = states[position_id]
    if current.status != STATUS_OPEN:
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_MANAGEMENT_ASSESSED, position_id,
                                         f"position is {current.status}, not OPEN -- a closed position cannot receive new management assessments")
    assessment = payload.get("assessment")
    if not isinstance(assessment, dict):
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_MANAGEMENT_ASSESSED, position_id, "malformed payload: missing assessment")

    updated = PositionLifecycle(
        position_id=current.position_id, session_id=current.session_id, status=current.status,
        entry=current.entry, legs=current.legs, opened_at=current.opened_at,
        thesis_evaluations=current.thesis_evaluations,
        management_assessments=current.management_assessments + (assessment,),
        closed_at=current.closed_at, exit_reason=current.exit_reason,
        final_thesis_status=current.final_thesis_status,
        latest_management_recommendation=assessment.get("recommendation"),
        realized_pnl=current.realized_pnl,
    )
    new_states = dict(states)
    new_states[position_id] = updated
    return new_states, TransitionResult(TRANSITION_ACCEPTED, EVENT_MANAGEMENT_ASSESSED, position_id, "management assessment recorded")


def _apply_position_closed(states: Dict[str, PositionLifecycle], payload: dict) -> Tuple[Dict[str, PositionLifecycle], TransitionResult]:
    position_id = payload.get("position_id")
    if not position_id or position_id not in states:
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_POSITION_CLOSED, position_id,
                                         "unknown position_id -- cannot close a position that was never opened")
    current = states[position_id]
    if current.status == STATUS_CLOSED:
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_POSITION_CLOSED, position_id,
                                         "position is already CLOSED -- CLOSED -> OPEN or a second independent close "
                                         "requires a NEW position identity, never a silent re-open")
    try:
        closed_at = payload["closed_at"]
        exit_reason = payload["exit_reason"]
    except KeyError:
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_POSITION_CLOSED, position_id, "malformed payload: missing required field")

    structured_exit_dict = payload.get("structured_exit")
    # Phase 15K: "best-known" realized_pnl for backward compatibility
    # with Phase 15J's existing Outcome Attribution consumer -- net if
    # known, else gross if known, else None (never fabricated). The
    # FULL gross/fees/slippage/net/per-leg breakdown lives in
    # structured_exit_dict regardless.
    realized_pnl = current.realized_pnl
    if structured_exit_dict is not None:
        realized_pnl = structured_exit_dict.get("net_realized_pnl")
        if realized_pnl is None:
            realized_pnl = structured_exit_dict.get("gross_realized_pnl")

    updated = PositionLifecycle(
        position_id=current.position_id, session_id=current.session_id, status=STATUS_CLOSED,
        entry=current.entry, legs=current.legs, opened_at=current.opened_at,
        thesis_evaluations=current.thesis_evaluations,
        management_assessments=current.management_assessments,
        closed_at=closed_at, exit_reason=exit_reason,
        final_thesis_status=current.final_thesis_status,
        latest_management_recommendation=current.latest_management_recommendation,
        realized_pnl=realized_pnl, outcome_attribution=current.outcome_attribution,
        structured_exit=structured_exit_dict,
    )
    new_states = dict(states)
    new_states[position_id] = updated
    return new_states, TransitionResult(TRANSITION_ACCEPTED, EVENT_POSITION_CLOSED, position_id, "position closed")


def _apply_outcome_attributed(states: Dict[str, PositionLifecycle], payload: dict) -> Tuple[Dict[str, PositionLifecycle], TransitionResult]:
    position_id = payload.get("position_id")
    if not position_id or position_id not in states:
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_OUTCOME_ATTRIBUTED, position_id,
                                         "unknown position_id -- cannot attribute an outcome for a position that was never opened")
    current = states[position_id]
    if current.status != STATUS_CLOSED:
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_OUTCOME_ATTRIBUTED, position_id,
                                         f"position is {current.status}, not CLOSED -- an outcome attribution requires a closed position "
                                         "(an open position has no outcome yet; see NOT_READY handling)")
    attribution = payload.get("attribution")
    if not isinstance(attribution, dict):
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_OUTCOME_ATTRIBUTED, position_id, "malformed payload: missing attribution")

    if current.outcome_attribution is not None:
        if current.outcome_attribution == attribution:
            return states, TransitionResult(TRANSITION_IDEMPOTENT, EVENT_OUTCOME_ATTRIBUTED, position_id,
                                             "duplicate OUTCOME_ATTRIBUTED with identical content -- a genuine retry, treated as a no-op")
        return states, TransitionResult(TRANSITION_REJECTED, EVENT_OUTCOME_ATTRIBUTED, position_id,
                                         "an outcome attribution already exists with DIFFERENT content -- immutable historical "
                                         "evidence can never be silently overwritten")

    updated = PositionLifecycle(
        position_id=current.position_id, session_id=current.session_id, status=current.status,
        entry=current.entry, legs=current.legs, opened_at=current.opened_at,
        thesis_evaluations=current.thesis_evaluations,
        management_assessments=current.management_assessments,
        closed_at=current.closed_at, exit_reason=current.exit_reason,
        final_thesis_status=current.final_thesis_status,
        latest_management_recommendation=current.latest_management_recommendation,
        realized_pnl=current.realized_pnl, outcome_attribution=attribution,
        structured_exit=current.structured_exit,
    )
    new_states = dict(states)
    new_states[position_id] = updated
    return new_states, TransitionResult(TRANSITION_ACCEPTED, EVENT_OUTCOME_ATTRIBUTED, position_id, "outcome attribution recorded")


def apply_event(
    states: Dict[str, PositionLifecycle], session_id: str, event_type: str, event_session_id: str, payload: dict,
) -> Tuple[Dict[str, PositionLifecycle], TransitionResult]:
    """The single reducer every live call AND every replay/hydration
    call goes through -- guarantees replay can never diverge from live
    behavior. `event_session_id` is the event's OWN session_id (as
    persisted); an event from another session is rejected outright,
    never silently applied to this session's state."""
    if event_session_id != session_id:
        return states, TransitionResult(TRANSITION_REJECTED, event_type, payload.get("position_id"),
                                         f"event belongs to session {event_session_id!r}, not this session {session_id!r}")
    if event_type == EVENT_POSITION_OPENED:
        return _apply_position_opened(states, session_id, payload)
    if event_type == EVENT_THESIS_EVALUATED:
        return _apply_thesis_evaluated(states, payload)
    if event_type == EVENT_MANAGEMENT_ASSESSED:
        return _apply_management_assessed(states, payload)
    if event_type == EVENT_POSITION_CLOSED:
        return _apply_position_closed(states, payload)
    if event_type == EVENT_OUTCOME_ATTRIBUTED:
        return _apply_outcome_attributed(states, payload)
    return states, TransitionResult(TRANSITION_REJECTED, event_type, payload.get("position_id"), f"unknown event_type: {event_type!r}")
