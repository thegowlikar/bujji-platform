"""D-8a: the real cost of a trade, summed from what the broker actually
reported.

PaperBroker computes a full ChargesBreakdown (brokerage, STT, exchange, GST,
SEBI, stamp duty) and a slippage figure for every fill, and files them in
`get_execution_report(client_order_id)`. `close_position()` has accepted
`fees=` and `slippage=` parameters the whole time. Nothing ever connected
them, so every structured exit was built with fees=None and every outcome
memory recorded a GROSS result as if it were net. On a four-leg round trip
that is eight sets of charges missing from the number Bujji will one day
learn from.

THE ABSENCE RULE, which is the whole reason this is a module and not three
inline lines: an order with no execution report contributes NOTHING and is
counted as missing. If NO order has a report, `fees` and `slippage` come
back None -- never 0.0. Zero fees is a claim that the trade was free;
None is the truth that we did not measure it. Feeding a silent 0.0 into
`close_position` would be exactly the "treat missing as zero" anti-pattern,
and it would look identical to a genuinely free trade forever after.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class ExecutionCostSummary:
    """Measured cost, plus the coverage that produced it. `fees`/`slippage`
    are None when nothing was measurable -- see the module docstring."""

    fees: Optional[float]
    slippage: Optional[float]
    orders_seen: int
    orders_with_report: int
    orders_missing_report: int
    orders_without_charges: int

    @property
    def is_complete(self) -> bool:
        """True only when every order contributed a real charges figure.
        Partial coverage is still recorded, but it is NOT complete and the
        caller should say so rather than presenting it as the full cost."""
        return self.orders_seen > 0 and self.orders_missing_report == 0 and self.orders_without_charges == 0

    def as_dict(self) -> dict:
        return {
            "fees": self.fees, "slippage": self.slippage, "orders_seen": self.orders_seen,
            "orders_with_report": self.orders_with_report,
            "orders_missing_report": self.orders_missing_report,
            "orders_without_charges": self.orders_without_charges,
            "is_complete": self.is_complete,
        }


def collect_execution_costs(broker: Any, client_order_ids: Iterable[str]) -> ExecutionCostSummary:
    """Sum the REAL charges and slippage the broker reported for these orders.

    Both entry and exit ids belong here: a position's cost is what it cost to
    get in AND out, and charging only one side understates every round trip.
    """
    ids = [oid for oid in dict.fromkeys(client_order_ids) if oid]
    if not hasattr(broker, "get_execution_report") or not ids:
        return ExecutionCostSummary(None, None, len(ids), 0, len(ids), 0)

    fees_total = 0.0
    slippage_total = 0.0
    with_report = missing = no_charges = 0
    saw_any_charge = False
    saw_any_slippage = False

    for order_id in ids:
        try:
            report = broker.get_execution_report(order_id)
        except Exception:  # noqa: BLE001 -- an unreadable report is a missing one, never a zero
            report = None
        if report is None:
            missing += 1
            continue
        with_report += 1

        charges = getattr(report, "charges", None)
        total = getattr(charges, "total", None) if charges is not None else None
        if total is None:
            no_charges += 1
        else:
            fees_total += float(total)
            saw_any_charge = True

        slippage = getattr(report, "slippage", None)
        if slippage is not None:
            slippage_total += abs(float(slippage))
            saw_any_slippage = True

    return ExecutionCostSummary(
        fees=fees_total if saw_any_charge else None,
        slippage=slippage_total if saw_any_slippage else None,
        orders_seen=len(ids), orders_with_report=with_report,
        orders_missing_report=missing, orders_without_charges=no_charges,
    )
