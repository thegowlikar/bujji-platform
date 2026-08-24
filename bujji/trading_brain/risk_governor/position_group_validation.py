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
    # EXIT_ATTEMPT_RECORDED -- M4b, authorised 2026-08-23. The SECOND narrow
    # extension of this frozen vocabulary; see
    # tests/test_frozen_vocabulary_extension.py, which authorises THIS change
    # specifically rather than unfreezing the file.
    #
    # WHY IT EXISTS. An exit is attempted, may be rejected, cancelled, time
    # out, or end UNKNOWN, and may then be retried. That history has to be
    # durable BEFORE any order is placed, or a crash leaves an order nothing
    # can find. The first M4b draft recorded it by minting a position group
    # per attempt, and that was wrong in a way worth writing down: an acked
    # but unfilled exit group folds to CONSTRUCTED, which is in
    # whole_book_margin_provider._ACTIVE_LIFECYCLE_STATES -- so the order that
    # REDUCES exposure was counted as exposure, doubling the measured book.
    # Group enumeration went from one group to one-per-attempt with it.
    #
    # WHY IT MINTS NOTHING. A position group means underlying exposure. An
    # exit attempt is not exposure; it is an event in the life of exposure
    # that already exists. So this event is appended to the ORIGINAL exposure
    # group, and the attempts of one exposure are ordered history on that one
    # group -- not siblings of it.
    #
    # WHY IT CANNOT CONTAMINATE ANYTHING. `apply_event_to_state` does not
    # recognise this type, so it mutates no leg, no lifecycle_state, and no
    # closure reason: the group folds exactly as it would without it. Margin,
    # reconciliation, group enumeration and reconstructed exposure therefore
    # see one group whose state is unchanged BY CONSTRUCTION, with no frozen
    # consumer modified. The exposure change itself is carried by
    # TARGET_GROUP_REDUCTION_APPLIED, which is what actually reduces a leg,
    # and which is unchanged here.
    "EXIT_ATTEMPT_RECORDED",
    # ORPHAN_EXPOSURE_RECORDED -- M4b, authorised 2026-08-23. The THIRD narrow
    # extension of this frozen vocabulary; see
    # tests/test_frozen_vocabulary_extension.py, which authorises THIS change
    # specifically rather than unfreezing the file.
    #
    # WHY IT EXISTS. The broker can hold a position no journal group claims --
    # exposure Bujji has no record of opening. The right risk action is to
    # flatten it (refusing leaves naked overnight option exposure), and the
    # right record is neither silence nor a minted group: silence makes the
    # flatten a side channel nothing can recover after a crash, and a minted
    # group makes Bujji claim it opened a position it did not -- the exact
    # synthetic-exposure defect the EXIT_ATTEMPT extension removed.
    #
    # SO IT IS A SESSION-SCOPED RECORD OF BROKER EXPOSURE. It lives ONLY under
    # a SESSION: identity (the writer-side invariant in position_group_scope
    # enforces the namespace; this validator enforces the payload), it is
    # invisible to apply_event_to_state, and position_group_ids() excludes its
    # scope -- margin, reconciliation, enumeration and reconstructed exposure
    # are untouched BY CONSTRUCTION. It is a representation of what the broker
    # holds, not a new store and not a synthetic margin position.
    #
    # ONLY A BROKER-CONFIRMED FLAT MAY TERMINALLY RESOLVE ONE. The
    # RESOLVED_FLAT branch below refuses any other broker_truth_state, so "we
    # flattened it and heard nothing since" cannot be written as resolution.
    "ORPHAN_EXPOSURE_RECORDED",
})

# What an orphan-exposure record may say. A CLOSED SET: the whole purpose of
# the record is to distinguish "the broker confirmed this is gone" from every
# weaker claim, and a free-text state would let the weak be written as the
# strong.
ORPHAN_DISCOVERED = "DISCOVERED"
ORPHAN_BROKER_OPEN = "BROKER_OPEN_CONFIRMED"
ORPHAN_BROKER_UNKNOWN = "BROKER_UNKNOWN_OBSERVED"
ORPHAN_RESOLVED_FLAT = "RESOLVED_FLAT"
_VALID_ORPHAN_RECORD_STATES = (
    ORPHAN_DISCOVERED, ORPHAN_BROKER_OPEN, ORPHAN_BROKER_UNKNOWN,
    ORPHAN_RESOLVED_FLAT,
)

# What an exit attempt may be. A CLOSED SET, because the whole purpose of the
# record is to distinguish "this order is finished with" from "nobody knows",
# and a free-text state would let the second be written as the first.
EXIT_ATTEMPT_INTENT = "INTENT"
EXIT_ATTEMPT_ACKED = "ACKED"
EXIT_ATTEMPT_FILLED = "FILLED"
EXIT_ATTEMPT_REJECTED = "REJECTED"
EXIT_ATTEMPT_CANCELLED = "CANCELLED"
EXIT_ATTEMPT_UNKNOWN = "UNKNOWN"
EXIT_ATTEMPT_RECONCILED = "RECONCILED"
_VALID_EXIT_ATTEMPT_STATES = (
    EXIT_ATTEMPT_INTENT, EXIT_ATTEMPT_ACKED, EXIT_ATTEMPT_FILLED,
    EXIT_ATTEMPT_REJECTED, EXIT_ATTEMPT_CANCELLED, EXIT_ATTEMPT_UNKNOWN,
    EXIT_ATTEMPT_RECONCILED,
)

# The states from which an attempt may never be retried, because the order is
# NOT known to be gone. UNKNOWN is deliberately absent from the terminal set
# below for the same reason.
EXIT_ATTEMPT_TERMINAL_STATES = (
    EXIT_ATTEMPT_REJECTED, EXIT_ATTEMPT_CANCELLED, EXIT_ATTEMPT_RECONCILED,
)

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
    # `broker_client_order_id`, deliberately NOT `client_order_id`: this is the
    # id of an EXIT order at the broker, not a leg of the exposure group. The
    # name keeps it out of the leg-existence check below, and out of the way of
    # anything that reads client_order_id expecting a leg.
    "EXIT_ATTEMPT_RECORDED": ("exit_attempt_id", "exposure_position_group_id",
                              "broker_client_order_id", "cause", "attempt_state",
                              "target_contract_id"),
    # The full orphan contract. Everything an operator or a restart needs to
    # act on the record without this process's memory: what the broker holds
    # (symbol, SIGNED quantity, contract), which session found it and when,
    # what evidence the discovery rests on, and what the broker last said.
    # Exit-attempt history and broker order ids live in the
    # EXIT_ATTEMPT_RECORDED events under the same session scope, linked by
    # orphan_id/target contract -- one journal, one authority.
    "ORPHAN_EXPOSURE_RECORDED": ("orphan_id", "session_id", "symbol",
                                 "signed_quantity", "contract_id",
                                 "discovered_at", "evidence_reference",
                                 "record_state", "broker_truth_state"),
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

    if event_type == "ORPHAN_EXPOSURE_RECORDED":
        if payload["record_state"] not in _VALID_ORPHAN_RECORD_STATES:
            raise IllegalEventError(
                f"ORPHAN_EXPOSURE_RECORDED.record_state must be one of "
                f"{_VALID_ORPHAN_RECORD_STATES}, got {payload['record_state']!r}")
        for field_name in ("orphan_id", "session_id", "symbol", "contract_id",
                           "evidence_reference"):
            if not payload[field_name]:
                raise IllegalEventError(
                    f"ORPHAN_EXPOSURE_RECORDED requires a non-empty "
                    f"{field_name} -- a record an operator cannot act on is a "
                    f"side channel, not evidence")
        orphan_quantity = payload["signed_quantity"]
        if not isinstance(orphan_quantity, int) or isinstance(orphan_quantity, bool):
            raise IllegalEventError(
                "ORPHAN_EXPOSURE_RECORDED.signed_quantity must be a signed "
                f"integer, got {orphan_quantity!r} -- the SIGN is what says "
                "whether flattening means buying or selling")
        if payload["record_state"] == ORPHAN_DISCOVERED and orphan_quantity == 0:
            raise IllegalEventError(
                "an orphan DISCOVERED with signed_quantity 0 is not exposure; "
                "recording it would let a no-op masquerade as a finding")
        orphan_discovered_raw = payload["discovered_at"]
        if not isinstance(orphan_discovered_raw, str):
            raise IllegalEventError(
                "ORPHAN_EXPOSURE_RECORDED.discovered_at must be an ISO-8601 string")
        try:
            orphan_parsed = datetime.fromisoformat(orphan_discovered_raw)
        except ValueError as exc:
            raise IllegalEventError(
                f"ORPHAN_EXPOSURE_RECORDED.discovered_at is not valid ISO-8601: {exc}")
        if orphan_parsed.tzinfo is None:
            raise IllegalEventError(
                "ORPHAN_EXPOSURE_RECORDED.discovered_at must be timezone-aware")
        # THE TERMINAL STATE IS EARNED, NOT ASSERTED. RESOLVED_FLAT with any
        # broker_truth_state other than CONFIRMED_FLAT is the exact lie this
        # record type exists to make unwritable: local intent, a swallowed
        # exception, or "we sent the exit and heard nothing" presenting as a
        # broker-confirmed flat.
        if (payload["record_state"] == ORPHAN_RESOLVED_FLAT
                and payload["broker_truth_state"] != "CONFIRMED_FLAT"):
            raise IllegalEventError(
                f"ORPHAN_EXPOSURE_RECORDED may only reach RESOLVED_FLAT with "
                f"broker_truth_state CONFIRMED_FLAT; got "
                f"{payload['broker_truth_state']!r}. UNKNOWN is not FLAT, and "
                f"neither is a submitted exit of unproven fate")
        # Session-scoped BY CONTRACT, like SESSION_TRANSITION: no group
        # preconditions apply. The writer-side invariant in
        # position_group_scope refuses this type on any position-group
        # identity; apply_event_to_state does not recognise it, so it cannot
        # move a leg or a lifecycle state anywhere.
        return

    if event_type == "EXIT_ATTEMPT_RECORDED":
        if payload["attempt_state"] not in _VALID_EXIT_ATTEMPT_STATES:
            raise IllegalEventError(
                f"EXIT_ATTEMPT_RECORDED.attempt_state must be one of "
                f"{_VALID_EXIT_ATTEMPT_STATES}, got {payload['attempt_state']!r}")
        if not payload["exit_attempt_id"]:
            raise IllegalEventError(
                "EXIT_ATTEMPT_RECORDED requires a non-empty exit_attempt_id -- it "
                "is what distinguishes one attempt from a retry, and without it "
                "two attempts collapse into one record")
        # ORPHAN EXPOSURE: the broker holds something no journal group claims.
        # Its attempt history is recorded against the SESSION identity, which
        # is NOT a position group -- position_group_ids() excludes it, so it
        # cannot reach margin, reconciliation, or reconstructed exposure.
        #
        # WHY ORPHANS ARE FLATTENED AT ALL, rather than refused: refusing
        # leaves naked overnight option exposure, which is a far worse outcome
        # than an exit whose provenance needs an operator to explain. The
        # position gets closed; the anomaly gets escalated.
        if str(payload["exposure_position_group_id"]).startswith("SESSION:"):
            return
        if current_state is None:
            raise IllegalEventError(
                "EXIT_ATTEMPT_RECORDED requires an already-minted group: an exit "
                "attempt is an event in the life of EXISTING exposure, and this "
                "event never mints one")
        if not current_state.constructed:
            raise IllegalEventError(
                "EXIT_ATTEMPT_RECORDED requires a constructed group -- there are "
                "no legs to exit before construction")
        # DELIBERATELY EXEMPT FROM TERMINALITY. A group reaches CLOSED the
        # moment its last leg is reduced, and the attempt that caused it still
        # has to be recordable afterwards -- as does a late reconciliation of
        # an attempt whose fate arrived after closure. It mutates nothing, so
        # allowing it past terminality cannot alter a terminal group's state.
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
