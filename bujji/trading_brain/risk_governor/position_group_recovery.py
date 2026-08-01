"""Position Group recovery — BUJJI Options OS v3, Numeric Risk Governor
Gate A.

Run once at process start, before any new mint is permitted. For every
leg left SUBMIT_PENDING_UNKNOWN or CANCEL_PENDING_UNKNOWN by a prior
crash, queries the broker directly for that exact client_order_id and
appends whatever conclusive event the broker's response supports.

Fill reconciliation ALWAYS runs first, unconditionally, whenever the
broker reports any filled_quantity > 0 -- regardless of the order's
terminal status. A broker report of "cancelled after partially
filling" must never silently drop the fill: the fill is recorded (and
SUBMIT_ACK recovered first if it was never durably written), and only
then is the terminal disposition (CANCEL_ACK / SUBMIT_FAILURE)
appended on top of that now-current state. This is what keeps the
journal's own ABORTED precondition (every leg conclusively resolved
AND zero net fill) honest -- a cancelled-with-fill leg can never be
mis-recorded in a way that makes the group look fully aborted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional, Protocol

from bujji.trading_brain.risk_governor.position_group_fill_reconciliation import compute_fill_delta
from bujji.trading_brain.risk_governor.position_group_fold import (
    LEG_CANCEL_PENDING_UNKNOWN,
    LEG_SUBMIT_PENDING_UNKNOWN,
    fold,
)
from bujji.journal.position_group_journal import PositionGroupJournal

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now(timezone.utc)


class BrokerOrderLookupResult(Protocol):
    client_order_id: str
    status: str
    filled_quantity: int
    average_price: Optional[float]
    found: bool


class BrokerOrderLookup(Protocol):
    def get_order(self, client_order_id: str) -> BrokerOrderLookupResult: ...


@dataclass(frozen=True)
class RecoveryOutcome:
    position_group_id: str
    client_order_id: str
    resolved: bool
    detail: str


def recover_group(
    journal: PositionGroupJournal,
    broker: BrokerOrderLookup,
    position_group_id: str,
    clock: Clock = _real_clock,
) -> List[RecoveryOutcome]:
    outcomes: List[RecoveryOutcome] = []
    state = fold(journal.read_events(position_group_id))

    for coid, leg in state.legs.items():
        if leg.submit_status not in (LEG_SUBMIT_PENDING_UNKNOWN, LEG_CANCEL_PENDING_UNKNOWN):
            continue

        result = broker.get_order(coid)

        if not result.found:
            ev = journal.append_event(
                position_group_id, "SUBMIT_FAILURE",
                f"{position_group_id}:SUBMIT_FAILURE:{coid}:recovery",
                {"client_order_id": coid, "failure_reason": "not found at broker during recovery",
                 "resolution_basis": "RECOVERY_CONFIRMED_NEVER_RECEIVED"},
                clock=clock,
            )
            outcomes.append(RecoveryOutcome(position_group_id, coid, ev is not None, "never received"))
            continue

        # Fill reconciliation ALWAYS runs first, regardless of terminal status.
        recovered_fill_delta = 0
        if result.filled_quantity > 0:
            current_leg = fold(journal.read_events(position_group_id)).legs[coid]
            if current_leg.submit_status == LEG_SUBMIT_PENDING_UNKNOWN or current_leg.submit_status == LEG_CANCEL_PENDING_UNKNOWN:
                # SUBMIT_ACK must exist before FILL_OBSERVED can validate
                # against this leg (validation requires an ACKED leg's
                # own SUBMIT_INTENT already resolved) -- recover it first
                # if the crash landed before we durably wrote one.
                if current_leg.submit_status == LEG_SUBMIT_PENDING_UNKNOWN:
                    journal.append_event(
                        position_group_id, "SUBMIT_ACK",
                        f"{position_group_id}:SUBMIT_ACK:{coid}:recovery",
                        {"client_order_id": coid, "broker_order_id": coid,
                         "broker_reported_status": result.status},
                        clock=clock,
                    )
            current_leg = fold(journal.read_events(position_group_id)).legs[coid]
            delta = compute_fill_delta(current_leg.fill, result.filled_quantity, result.average_price)
            if delta.delta_quantity > 0:
                journal.append_event(
                    position_group_id, "FILL_OBSERVED",
                    f"{position_group_id}:FILL_OBSERVED:{coid}:{result.filled_quantity}:{result.average_price}",
                    {"client_order_id": coid,
                     "cumulative_filled_quantity_after": result.filled_quantity,
                     "cumulative_average_fill_price_after": result.average_price,
                     "delta_quantity": delta.delta_quantity,
                     "delta_value": delta.delta_value,
                     "delta_cost_basis_status": delta.cost_basis_status,
                     "fill_price": result.average_price},
                    clock=clock,
                )
                recovered_fill_delta = delta.delta_quantity

        if result.status == "CANCELLED":
            current_leg = fold(journal.read_events(position_group_id)).legs[coid]
            if current_leg.submit_status != LEG_CANCEL_PENDING_UNKNOWN:
                # No CANCEL_INTENT was ever durably recorded by us (the
                # broker/operator cancelled independently) -- recover the
                # intent first so CANCEL_ACK has a legal predecessor.
                journal.append_event(
                    position_group_id, "CANCEL_INTENT",
                    f"{position_group_id}:CANCEL_INTENT:{coid}:recovery",
                    {"client_order_id": coid}, clock=clock,
                )
            ev = journal.append_event(
                position_group_id, "CANCEL_ACK",
                f"{position_group_id}:CANCEL_ACK:{coid}:recovery",
                {"client_order_id": coid, "cancelled_quantity": result.filled_quantity},
                clock=clock,
            )
            detail = (f"cancelled at broker, recovered fill delta={recovered_fill_delta}"
                      if recovered_fill_delta else "cancelled at broker")
            outcomes.append(RecoveryOutcome(position_group_id, coid, ev is not None, detail))
            continue

        if result.status == "REJECTED":
            ev = journal.append_event(
                position_group_id, "SUBMIT_FAILURE",
                f"{position_group_id}:SUBMIT_FAILURE:{coid}:recovery",
                {"client_order_id": coid, "failure_reason": "broker reports rejected",
                 "resolution_basis": "CONFIRMED_REJECTION"},
                clock=clock,
            )
            outcomes.append(RecoveryOutcome(position_group_id, coid, ev is not None, "confirmed rejection"))
            continue

        if result.status in ("FILLED", "PARTIAL"):
            outcomes.append(RecoveryOutcome(
                position_group_id, coid, True,
                f"recovered fill delta={recovered_fill_delta}" if recovered_fill_delta
                else "no new fill delta (already recorded)",
            ))
            continue

        journal.append_event(
            position_group_id, "RECONCILIATION_ATTEMPTED",
            f"{position_group_id}:RECONCILIATION_ATTEMPTED:{coid}:{clock().isoformat()}",
            {"client_order_id": coid, "outcome": "INCONCLUSIVE"}, clock=clock,
        )
        outcomes.append(RecoveryOutcome(position_group_id, coid, False, "inconclusive"))

    return outcomes
