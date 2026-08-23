"""Position Group event validation — BUJJI Options OS v3, Numeric Risk
Governor Gate A.

Called ONLY from inside position_group_journal.py's open BEGIN
IMMEDIATE transaction, after the idempotency check, against the
CURRENT folded state read in that same transaction. Malformed or
illegal events never reach INSERT.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from bujji.trading_brain.risk_governor.position_group_fold import (
    LEG_ACKED,
    LEG_CANCEL_PENDING_UNKNOWN,
    LEG_CANCELLED,
    LEG_NOT_SUBMITTED,
    LEG_SUBMIT_FAILED,
    LEG_SUBMIT_PENDING_UNKNOWN,
    LIFECYCLE_PENDING_FINAL_RECONCILIATION,
    PositionGroupState,
    TERMINAL_LIFECYCLE_STATES,
    net_quantity,
)

KNOWN_EVENT_TYPES = frozenset({
    "MINTED", "CONSTRUCTED",
    "SUBMIT_INTENT", "SUBMIT_ACK", "SUBMIT_FAILURE",
    "FILL_OBSERVED",
    "CANCEL_INTENT", "CANCEL_ACK",
    "TARGET_GROUP_REDUCTION_APPLIED",
    "RECONCILIATION_ATTEMPTED",
    "FINAL_RECONCILIATION_CONFIRMED",
    "OPERATOR_CORRECTION_RECORDED",
    # SESSION_TRANSITION -- M4, authorised 2026-08-23. A NARROW, scoped
    # extension of this frozen vocabulary; see the block below and
    # tests/test_frozen_vocabulary_extension.py, which authorises THIS change
    # specifically rather than unfreezing the file.
    #
    # WHY IT BELONGS HERE AND NOT IN A SECOND STORE. Session lifecycle and
    # position lifecycle are the same question asked at two scopes, and the
    # whole architecture effort is about removing second authorities over one
    # fact. A separate session store would be exactly that.
    #
    # WHY IT NEEDS NO POSITION GROUP. A no-trade day mints no group at all, so
    # a session-scoped event cannot require one -- and refusal, startup and
    # completion sessions must have durable history too. Session events use a
    # `SESSION:<session_id>` identity in the position_group_id column, which
    # is a distinct namespace, never an exposure group.
    #
    # WHY IT CANNOT POLLUTE MARGIN OR RECONCILIATION. `apply_event_to_state`
    # does not recognise this type, so a session-scoped identity folds with
    # constructed=False -> LIFECYCLE_MINTED, and MINTED is not in
    # whole_book_margin_provider._ACTIVE_LIFECYCLE_STATES. Every group/margin
    # query therefore skips it BY CONSTRUCTION, with no frozen consumer
    # changed and no synthetic exposure group created.
    "SESSION_TRANSITION",
})

# CLOSED and ABORTED are NOT in this vocabulary -- both are purely derived
# by position_group_fold.py, never directly appendable. Attempting to
# append either raises IllegalEventError as an unknown event type.

_REQUIRED_FIELDS = {
    "MINTED": ("plan_id", "strategy_id", "underlying"),
    "CONSTRUCTED": ("contract_client_order_map",),
    "SUBMIT_INTENT": ("client_order_id",),
    "SUBMIT_ACK": ("client_order_id", "broker_order_id", "broker_reported_status"),
    "SUBMIT_FAILURE": ("client_order_id", "failure_reason", "resolution_basis"),
    "FILL_OBSERVED": (
        "client_order_id", "cumulative_filled_quantity_after",
        "delta_quantity", "delta_cost_basis_status",
    ),
    # SESSION_TRANSITION (M4): who, from what, to what, why, and on what
    # evidence. `evidence_ref` is the reference to whatever established the
    # transition -- a reconciliation verdict, a fill's event id, a broker-truth
    # result -- so a reader can follow any state back to what caused it.
    "SESSION_TRANSITION": ("session_id", "prior_state", "next_state", "cause",
                           "evidence_ref"),
    "CANCEL_INTENT": ("client_order_id",),
    "CANCEL_ACK": ("client_order_id",),
    "TARGET_GROUP_REDUCTION_APPLIED": (
        "source_client_order_id", "source_position_group_id",
        "target_contract_id", "reduced_quantity_delta",
    ),
    "RECONCILIATION_ATTEMPTED": ("client_order_id", "outcome"),
    "FINAL_RECONCILIATION_CONFIRMED": (
        "broker_confirmation_reference", "confirmed_by", "confirmed_at",
    ),
    "OPERATOR_CORRECTION_RECORDED": (
        "correcting_event_type", "correcting_payload", "operator_id",
        "justification", "evidence_reference", "reviewed_at",
    ),
}

_VALID_RESOLUTION_BASES = ("CONFIRMED_REJECTION", "RECOVERY_CONFIRMED_NEVER_RECEIVED")
_FILL_ADMITTED_FROM = (LEG_ACKED, LEG_CANCEL_PENDING_UNKNOWN, LEG_CANCELLED)
_ALLOWED_CORRECTING_EVENT_TYPES = ("FILL_OBSERVED", "TARGET_GROUP_REDUCTION_APPLIED")


class IllegalEventError(Exception):
    """Raised by validate_event() -- the caller must roll back and
    never insert this event."""


def validate_event(
    event_type: str,
    payload: Dict,
    recorded_at: datetime,
    current_state: Optional[PositionGroupState],
) -> None:
    if event_type not in KNOWN_EVENT_TYPES:
        raise IllegalEventError(f"unknown event_type: {event_type!r}")

    if recorded_at.tzinfo is None:
        raise IllegalEventError("recorded_at must be timezone-aware; naive datetimes are never trusted")

    missing = [f for f in _REQUIRED_FIELDS[event_type] if f not in payload]
    if missing:
        raise IllegalEventError(f"{event_type} missing required payload fields: {missing}")

    if event_type == "SUBMIT_FAILURE" and payload["resolution_basis"] not in _VALID_RESOLUTION_BASES:
        raise IllegalEventError(
            f"SUBMIT_FAILURE.resolution_basis must be one of {_VALID_RESOLUTION_BASES}, "
            f"got {payload['resolution_basis']!r}"
        )

    if event_type == "SESSION_TRANSITION":
        # SESSION-SCOPED, so the position-group preconditions below do not
        # apply: there may be no group at all (a no-trade day), and a session
        # continues past any single group's terminality. It mutates no leg and
        # no lifecycle_state -- `apply_event_to_state` does not recognise it --
        # so it cannot alter what any group/margin/reconciliation query sees.
        if payload["prior_state"] == payload["next_state"]:
            raise IllegalEventError(
                f"SESSION_TRANSITION from {payload['prior_state']!r} to itself "
                f"carries no information; a state that did not change is not a "
                f"transition")
        return

    if event_type == "MINTED":
        if current_state is not None:
            raise IllegalEventError("group already has a MINTED event; cannot mint twice")
        return

    # every other event_type requires a MINTED group to already exist
    if current_state is None:
        raise IllegalEventError(f"{event_type} requires an already-minted group")

    # Terminality: once CLOSED or ABORTED, no ordinary event may mutate
    # the group. OPERATOR_CORRECTION_RECORDED is the sole, audit-only
    # exception (it is explicitly ABOUT a terminal group and never
    # mutates legs/lifecycle_state/closure_reason -- see its own branch
    # in position_group_fold.py).
    if current_state.lifecycle_state in TERMINAL_LIFECYCLE_STATES and event_type != "OPERATOR_CORRECTION_RECORDED":
        raise IllegalEventError(
            f"{event_type} rejected: group is terminal ({current_state.lifecycle_state})"
        )

    if event_type == "FINAL_RECONCILIATION_CONFIRMED":
        if current_state.lifecycle_state != LIFECYCLE_PENDING_FINAL_RECONCILIATION:
            raise IllegalEventError(
                f"FINAL_RECONCILIATION_CONFIRMED illegal from lifecycle_state "
                f"{current_state.lifecycle_state} (must be PENDING_FINAL_RECONCILIATION)"
            )
        return

    if event_type == "OPERATOR_CORRECTION_RECORDED":
        correcting_type = payload["correcting_event_type"]
        if correcting_type not in _ALLOWED_CORRECTING_EVENT_TYPES:
            raise IllegalEventError(
                f"OPERATOR_CORRECTION_RECORDED.correcting_event_type must be one of "
                f"{_ALLOWED_CORRECTING_EVENT_TYPES}, got {correcting_type!r}"
            )
        if not payload["evidence_reference"]:
            raise IllegalEventError("OPERATOR_CORRECTION_RECORDED requires a non-empty evidence_reference")
        reviewed_at_raw = payload["reviewed_at"]
        if not isinstance(reviewed_at_raw, str):
            raise IllegalEventError("OPERATOR_CORRECTION_RECORDED.reviewed_at must be an ISO-8601 string")
        try:
            parsed = datetime.fromisoformat(reviewed_at_raw)
        except ValueError as exc:
            raise IllegalEventError(f"OPERATOR_CORRECTION_RECORDED.reviewed_at is not valid ISO-8601: {exc}")
        if parsed.tzinfo is None:
            raise IllegalEventError("OPERATOR_CORRECTION_RECORDED.reviewed_at must be timezone-aware")
        return

    if event_type == "CONSTRUCTED":
        if current_state.constructed:
            raise IllegalEventError("group already constructed; cannot construct twice")
        return

    if event_type in ("SUBMIT_INTENT", "SUBMIT_ACK", "SUBMIT_FAILURE", "CANCEL_INTENT",
                      "CANCEL_ACK", "FILL_OBSERVED"):
        coid = payload["client_order_id"]
        leg = current_state.legs.get(coid)
        if leg is None:
            raise IllegalEventError(f"{event_type} references unknown client_order_id {coid!r}")

        if event_type == "SUBMIT_INTENT" and leg.submit_status != LEG_NOT_SUBMITTED:
            raise IllegalEventError(
                f"SUBMIT_INTENT illegal from status {leg.submit_status} for {coid}"
            )
        if event_type in ("SUBMIT_ACK", "SUBMIT_FAILURE") and leg.submit_status not in (
            LEG_SUBMIT_PENDING_UNKNOWN,
        ):
            raise IllegalEventError(
                f"{event_type} illegal from status {leg.submit_status} for {coid} "
                f"(must follow an unresolved SUBMIT_INTENT)"
            )
        if event_type == "CANCEL_INTENT" and leg.submit_status not in (LEG_ACKED, LEG_SUBMIT_PENDING_UNKNOWN):
            raise IllegalEventError(
                f"CANCEL_INTENT illegal from status {leg.submit_status} for {coid}"
            )
        if event_type == "CANCEL_ACK" and leg.submit_status != LEG_CANCEL_PENDING_UNKNOWN:
            raise IllegalEventError(
                f"CANCEL_ACK illegal from status {leg.submit_status} for {coid} "
                f"(must follow a CANCEL_INTENT)"
            )
        if event_type == "FILL_OBSERVED":
            if leg.submit_status not in _FILL_ADMITTED_FROM:
                raise IllegalEventError(
                    f"FILL_OBSERVED rejected from status {leg.submit_status} for {coid} "
                    f"(admitted only from {_FILL_ADMITTED_FROM})"
                )

            new_qty = payload["cumulative_filled_quantity_after"]
            if isinstance(new_qty, bool) or not isinstance(new_qty, int) or new_qty < 0:
                raise IllegalEventError(
                    f"FILL_OBSERVED.cumulative_filled_quantity_after must be a non-negative int, got {new_qty!r}"
                )
            price = payload.get("cumulative_average_fill_price_after")
            if price is not None and (isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0):
                raise IllegalEventError(
                    f"FILL_OBSERVED.cumulative_average_fill_price_after must be a non-negative number, got {price!r}"
                )

            expected_delta = new_qty - leg.fill.cumulative_filled_quantity
            if expected_delta < 0:
                raise IllegalEventError(
                    f"FILL_OBSERVED non-monotonic for {coid}: reported {new_qty}, "
                    f"journal already has {leg.fill.cumulative_filled_quantity}"
                )
            if payload["delta_quantity"] != expected_delta:
                raise IllegalEventError(
                    f"FILL_OBSERVED.delta_quantity {payload['delta_quantity']!r} disagrees with the "
                    f"recomputed delta {expected_delta} for {coid}"
                )
            if expected_delta == 0:
                raise IllegalEventError(
                    f"zero-delta FILL_OBSERVED with a new idempotency_key carries no new information "
                    f"for {coid} -- replay the ORIGINAL event's own idempotency_key instead"
                )
        return

    if event_type == "TARGET_GROUP_REDUCTION_APPLIED":
        target_contract_id = payload["target_contract_id"]
        matching = [leg for leg in current_state.legs.values() if leg.contract_id == target_contract_id]
        if not matching:
            raise IllegalEventError(
                f"TARGET_GROUP_REDUCTION_APPLIED references unknown target_contract_id {target_contract_id!r}"
            )
        leg = matching[0]
        delta = payload["reduced_quantity_delta"]
        if delta <= 0:
            raise IllegalEventError("reduced_quantity_delta must be positive")
        if delta > net_quantity(leg):
            raise IllegalEventError(
                f"reduced_quantity_delta {delta} exceeds target leg's current net quantity "
                f"{net_quantity(leg)}"
            )
        return

    # RECONCILIATION_ATTEMPTED, CLOSED, ABORTED: no further state precondition beyond
    # "group already minted", already checked above.
    return
