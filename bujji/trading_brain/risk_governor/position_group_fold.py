"""Position Group fold — BUJJI Options OS v3, Numeric Risk Governor
Gate A.

`apply_event_to_state(state, event_type, payload, position_group_id)`
is the ONE place per-event application logic lives -- a pure function,
no I/O, no clock. `fold(events)` is simply this function reduced over
an ordered event list, starting from `None`. The journal's own
transactional validation (position_group_journal.py) uses this same
function to build a STAGED combined view when validating a linked
batch, so there is exactly one implementation of "what does this event
do to a group's state," never two independently-maintained copies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

LIFECYCLE_MINTED = "MINTED"
LIFECYCLE_CONSTRUCTED = "CONSTRUCTED"
LIFECYCLE_PARTIALLY_OPEN = "PARTIALLY_OPEN"
LIFECYCLE_OPEN = "OPEN"
LIFECYCLE_CLOSED = "CLOSED"
LIFECYCLE_ABORTED = "ABORTED"
LIFECYCLE_UNRESOLVED = "UNRESOLVED"
LIFECYCLE_PENDING_FINAL_RECONCILIATION = "PENDING_FINAL_RECONCILIATION"

TERMINAL_LIFECYCLE_STATES = (LIFECYCLE_CLOSED, LIFECYCLE_ABORTED)

LEG_NOT_SUBMITTED = "NOT_SUBMITTED"
LEG_SUBMIT_PENDING_UNKNOWN = "SUBMIT_PENDING_UNKNOWN"
LEG_SUBMIT_FAILED = "SUBMIT_FAILED"
LEG_ACKED = "ACKED"
LEG_CANCEL_PENDING_UNKNOWN = "CANCEL_PENDING_UNKNOWN"
LEG_CANCELLED = "CANCELLED"

_PENDING_STATUSES = (LEG_SUBMIT_PENDING_UNKNOWN, LEG_CANCEL_PENDING_UNKNOWN)


@dataclass(frozen=True)
class LegFillState:
    client_order_id: str
    cumulative_filled_quantity: int = 0
    cumulative_average_fill_price: Optional[float] = None
    cost_untrusted: bool = False
    reduced_quantity: int = 0  # cumulative TARGET_GROUP_REDUCTION_APPLIED applied to this leg


@dataclass(frozen=True)
class LegState:
    client_order_id: str
    contract_id: Optional[str]
    requested_quantity: Optional[int]
    submit_status: str
    fill: LegFillState
    action: Optional[str] = None
    target_position_group_id: Optional[str] = None
    target_contract_id: Optional[str] = None
    flip_link_id: Optional[str] = None


@dataclass(frozen=True)
class PositionGroupState:
    position_group_id: str
    lifecycle_state: str
    legs: Dict[str, LegState] = field(default_factory=dict)
    plan_id: Optional[str] = None
    strategy_id: Optional[str] = None
    underlying: Optional[str] = None
    closure_reason: Optional[str] = None
    cost_untrusted: bool = False
    unresolved_reason: Optional[str] = None
    constructed: bool = False
    ever_had_any_fill: bool = False
    final_reconciliation_confirmed: bool = False
    operator_corrections: Tuple[Dict, ...] = ()


def net_quantity(leg: LegState) -> int:
    return leg.fill.cumulative_filled_quantity - leg.fill.reduced_quantity


def fold(events: List) -> PositionGroupState:
    """`events` must already be ordered by sequence_no (the journal
    guarantees this on read)."""
    if not events:
        raise ValueError("fold() requires at least one event")
    position_group_id = events[0].position_group_id
    state: Optional[PositionGroupState] = None
    for ev in events:
        state = apply_event_to_state(state, ev.event_type, ev.payload, position_group_id)
    assert state is not None
    return state


def apply_event_to_state(
    state: Optional[PositionGroupState],
    event_type: str,
    payload: Dict,
    position_group_id: str,
) -> PositionGroupState:
    if state is None:
        state = PositionGroupState(position_group_id=position_group_id, lifecycle_state=LIFECYCLE_MINTED)

    legs = dict(state.legs)
    plan_id, strategy_id, underlying = state.plan_id, state.strategy_id, state.underlying
    closure_reason = state.closure_reason
    constructed = state.constructed
    ever_had_any_fill = state.ever_had_any_fill
    final_reconciliation_confirmed = state.final_reconciliation_confirmed
    operator_corrections = state.operator_corrections

    if event_type == "MINTED":
        plan_id = payload.get("plan_id")
        strategy_id = payload.get("strategy_id")
        underlying = payload.get("underlying")

    elif event_type == "CONSTRUCTED":
        constructed = True
        for contract_id, client_order_id in payload.get("contract_client_order_map", {}).items():
            legs[client_order_id] = LegState(
                client_order_id=client_order_id,
                contract_id=contract_id,
                requested_quantity=payload.get("requested_quantities", {}).get(client_order_id),
                submit_status=LEG_NOT_SUBMITTED,
                fill=LegFillState(client_order_id=client_order_id),
                action=payload.get("actions", {}).get(client_order_id),
                target_position_group_id=payload.get("target_position_group_ids", {}).get(client_order_id),
                target_contract_id=payload.get("target_contract_ids", {}).get(client_order_id),
                flip_link_id=payload.get("flip_link_ids", {}).get(client_order_id),
            )

    elif event_type == "SUBMIT_INTENT":
        coid = payload["client_order_id"]
        leg = legs.get(coid)
        if leg is not None:
            legs[coid] = _with_submit_status(leg, LEG_SUBMIT_PENDING_UNKNOWN)

    elif event_type == "SUBMIT_ACK":
        coid = payload["client_order_id"]
        leg = legs.get(coid)
        if leg is not None:
            legs[coid] = _with_submit_status(leg, LEG_ACKED)

    elif event_type == "SUBMIT_FAILURE":
        coid = payload["client_order_id"]
        leg = legs.get(coid)
        if leg is not None and payload.get("resolution_basis") in (
            "CONFIRMED_REJECTION", "RECOVERY_CONFIRMED_NEVER_RECEIVED",
        ):
            legs[coid] = _with_submit_status(leg, LEG_SUBMIT_FAILED)

    elif event_type == "CANCEL_INTENT":
        coid = payload["client_order_id"]
        leg = legs.get(coid)
        if leg is not None and leg.submit_status not in (LEG_SUBMIT_FAILED, LEG_CANCELLED):
            legs[coid] = _with_submit_status(leg, LEG_CANCEL_PENDING_UNKNOWN)

    elif event_type == "CANCEL_ACK":
        coid = payload["client_order_id"]
        leg = legs.get(coid)
        if leg is not None:
            legs[coid] = _with_submit_status(leg, LEG_CANCELLED)

    elif event_type == "FILL_OBSERVED":
        coid = payload["client_order_id"]
        leg = legs.get(coid)
        if leg is not None:
            new_qty = payload["cumulative_filled_quantity_after"]
            if new_qty > 0:
                ever_had_any_fill = True
            new_fill = LegFillState(
                client_order_id=coid,
                cumulative_filled_quantity=new_qty,
                cumulative_average_fill_price=payload.get("cumulative_average_fill_price_after"),
                cost_untrusted=(payload.get("delta_cost_basis_status") == "UNTRUSTED_INSUFFICIENT_PRICE_DATA")
                or leg.fill.cost_untrusted,
                reduced_quantity=leg.fill.reduced_quantity,
            )
            legs[coid] = _with_fill(leg, new_fill)

    elif event_type == "TARGET_GROUP_REDUCTION_APPLIED":
        target_contract_id = payload.get("target_contract_id")
        for coid, leg in list(legs.items()):
            if leg.contract_id == target_contract_id:
                delta = payload.get("reduced_quantity_delta", 0)
                new_fill = LegFillState(
                    client_order_id=leg.fill.client_order_id,
                    cumulative_filled_quantity=leg.fill.cumulative_filled_quantity,
                    cumulative_average_fill_price=leg.fill.cumulative_average_fill_price,
                    cost_untrusted=leg.fill.cost_untrusted,
                    reduced_quantity=leg.fill.reduced_quantity + delta,
                )
                legs[coid] = _with_fill(leg, new_fill)
                if new_fill.cumulative_filled_quantity > 0:
                    ever_had_any_fill = True
                break

    elif event_type == "RECONCILIATION_ATTEMPTED":
        pass

    elif event_type == "FINAL_RECONCILIATION_CONFIRMED":
        # Precondition (current_state.lifecycle_state == PENDING_FINAL_RECONCILIATION)
        # is enforced by validate_event() before this is ever applied -- by
        # the time we get here, it is safe to simply record the fact.
        final_reconciliation_confirmed = True
        closure_reason = "FINAL_RECONCILIATION_CONFIRMED"

    elif event_type == "OPERATOR_CORRECTION_RECORDED":
        # Audit-only: never mutates legs, lifecycle_state, or closure_reason.
        operator_corrections = operator_corrections + (dict(payload),)

    lifecycle_state, unresolved_reason = _derive_lifecycle_state(
        constructed, legs, ever_had_any_fill, final_reconciliation_confirmed
    )
    cost_untrusted = any(leg.fill.cost_untrusted for leg in legs.values())

    return PositionGroupState(
        position_group_id=position_group_id,
        lifecycle_state=lifecycle_state,
        legs=legs,
        plan_id=plan_id,
        strategy_id=strategy_id,
        underlying=underlying,
        closure_reason=closure_reason,
        cost_untrusted=cost_untrusted,
        unresolved_reason=unresolved_reason,
        constructed=constructed,
        ever_had_any_fill=ever_had_any_fill,
        final_reconciliation_confirmed=final_reconciliation_confirmed,
        operator_corrections=operator_corrections,
    )


def _with_submit_status(leg: LegState, status: str) -> LegState:
    return LegState(
        client_order_id=leg.client_order_id, contract_id=leg.contract_id,
        requested_quantity=leg.requested_quantity, submit_status=status, fill=leg.fill,
        action=leg.action, target_position_group_id=leg.target_position_group_id,
        target_contract_id=leg.target_contract_id, flip_link_id=leg.flip_link_id,
    )


def _with_fill(leg: LegState, fill: LegFillState) -> LegState:
    return LegState(
        client_order_id=leg.client_order_id, contract_id=leg.contract_id,
        requested_quantity=leg.requested_quantity, submit_status=leg.submit_status, fill=fill,
        action=leg.action, target_position_group_id=leg.target_position_group_id,
        target_contract_id=leg.target_contract_id, flip_link_id=leg.flip_link_id,
    )


def _derive_lifecycle_state(
    constructed: bool,
    legs: Dict[str, LegState],
    ever_had_any_fill: bool,
    final_reconciliation_confirmed: bool,
) -> Tuple[str, Optional[str]]:
    """CLOSED and ABORTED are now PURELY derived -- there is no directly-
    appendable event type for either. CLOSED follows only from real,
    individually-validated fill/reduction facts reaching net-zero.
    ABORTED follows only from an explicit FINAL_RECONCILIATION_CONFIRMED
    event, itself only appendable once every leg is conclusively resolved
    at zero net fill with no fill ever having occurred -- see
    PENDING_FINAL_RECONCILIATION below, the fail-closed, non-terminal
    state a zero-fill cancellation/rejection now lands in instead."""
    if final_reconciliation_confirmed:
        return LIFECYCLE_ABORTED, None

    if not constructed:
        return LIFECYCLE_MINTED, None
    if not legs:
        return LIFECYCLE_CONSTRUCTED, None

    any_pending = any(leg.submit_status in _PENDING_STATUSES for leg in legs.values())
    if any_pending:
        return LIFECYCLE_UNRESOLVED, "AT_LEAST_ONE_LEG_SUBMIT_OR_CANCEL_PENDING_UNKNOWN"

    all_net_zero = all(net_quantity(leg) == 0 for leg in legs.values())
    if ever_had_any_fill and all_net_zero:
        return LIFECYCLE_CLOSED, None

    any_net_positive = any(net_quantity(leg) > 0 for leg in legs.values())
    if any_net_positive:
        all_at_requested = all(
            leg.requested_quantity is not None and net_quantity(leg) == leg.requested_quantity
            for leg in legs.values()
        )
        return (LIFECYCLE_OPEN if all_at_requested else LIFECYCLE_PARTIALLY_OPEN), None

    all_conclusively_zero_net = all(
        leg.submit_status in (LEG_SUBMIT_FAILED, LEG_CANCELLED) and net_quantity(leg) == 0
        for leg in legs.values()
    )
    if all_conclusively_zero_net and not ever_had_any_fill:
        return LIFECYCLE_PENDING_FINAL_RECONCILIATION, None

    return LIFECYCLE_CONSTRUCTED, None
