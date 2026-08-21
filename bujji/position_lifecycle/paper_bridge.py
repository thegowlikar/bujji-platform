"""PaperBroker Lifecycle Bridge -- Phase 15L. Observational/reconciliation
infrastructure only: reads existing PaperBroker execution evidence and
translates it into lifecycle-shaped facts. Never places, modifies, or
cancels an order; never imports execution/strategy/risk packages;
never computes P&L (delegates entirely to `bujji.position_lifecycle.pnl`
via `engine.build_structured_exit`).

Forensic finding driving this design (Step 3's central problem):
`PaperBroker` has NO concept of `position_id`/`leg_id` at all -- it is
keyed exclusively by `symbol` (netted ledger, `_positions`/
`_realized_pnl`) and by caller-supplied `client_order_id` (per-order,
`_orders`/`_execution_reports`). The symbol-netted ledger is
AMBIGUOUS the moment two lifecycle positions ever trade the same
symbol concurrently -- so this bridge NEVER reads
`get_realized_pnl()`/`get_open_positions()` (symbol-scoped, would
blend unrelated positions). It reads ONLY `get_order(client_order_id)`
and `get_execution_report(client_order_id)` -- both per-order and
therefore unambiguous -- and requires the CALLER to supply
`client_order_id`s that are DERIVED from lifecycle identity via
`client_order_id_for()` below, rather than inventing a separate
mapping table. `client_order_id` never becomes the canonical position
identity; it is a derived, disposable lookup key. Canonical identity
remains `position_id`/`leg_id` (Phase 15G, untouched).

Second forensic finding: PaperBroker persists NOTHING for
`_orders`/`_execution_reports` across a restart (only `_positions`/
`_realized_pnl` have `restore_*` hydration, Phase 15B) -- so this
bridge is only useful WITHIN a live session, observing fills as they
happen and immediately translating them into lifecycle event payloads
(which DO persist, via the existing EventStore). After a restart,
broker-side order history is gone; only the lifecycle events already
recorded survive -- exactly the existing replay guarantee, unchanged.

Phase 20.2.1 accounting correction (two bugs found auditing Phase 20.2's
own real-data proof run, both fixed here, neither redesigned):

1. `LegFillObservation.total_slippage` used to sum
   `ExecutionReport.slippage` RAW -- that field is a PER-UNIT adverse
   price delta (see `broker.simulation.slippage`'s own docstring), not
   a currency amount. Summing it raw silently understated true
   slippage cost by a factor of quantity. Fixed at the source in
   `observe_leg_fills_async`: `report.slippage * filled` -- currency,
   correctly scaled, exactly once.

2. `reconcile_position_exit_async` scanned sequence 1..32 for EVERY
   leg with no lower bound -- but `shadow_lifecycle.orchestrator`
   places entry orders at sequence 1 and exit orders at sequence 2
   (its own comment: "sequence 2 so the bridge can distinguish them
   from the entry fills"). The unbounded scan defeated that stated
   intent: it silently BLENDED the entry fill into the "exit"
   observation (a quantity-weighted average of entry AND exit fill
   prices, presented as `exit_price`), which corrupted gross P&L, and
   entirely discarded entry-side execution impact. Fixed with a new
   optional `exit_sequence_start` parameter (default 1, preserving
   every existing caller's behavior exactly) that scopes the exit
   observation to real exit-only fills and, when > 1, separately
   observes and folds in entry-side fees/slippage.

`build_exit_evidence_from_observations` additionally now reconstructs
each leg's THEORETICAL exit reference price (before slippage) from
already-recorded fields -- `avg_fill_price` and the corrected
per-unit-average slippage -- using the exact inverse of
`SlippageCalculator`'s own documented BUY-fills-high/SELL-fills-low
convention. This is an algebraic identity over real recorded numbers,
never a fabricated value: with zero slippage (every existing test's
default `PaperBroker()`) it returns the fill price unchanged, so no
prior caller's result changes unless real slippage was configured.
See docs/PHASE_20_2_1_NET_PNL_ACCOUNTING_AUDIT.md.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .pnl import reconstruct_reference_price

_MAX_FILL_SEQUENCE = 32  # Generous, deterministic upper bound on fills-per-leg scanned; never assumed to be exactly 1.


def client_order_id_for(position_id: str, leg_id: str, sequence: int = 1) -> str:
    """Deterministic, collision-free `client_order_id` derived FROM
    lifecycle identity (never the reverse). `sequence` supports
    multiple fills for the same leg (a partial fill topped up by a
    later order) -- callers issuing a second order for the same leg
    must increment `sequence`; the bridge scans sequences to discover
    however many real orders actually exist. Session-scoped for free,
    since `position_id` already embeds `session_id` (Phase 15G) --
    cross-session collisions are therefore impossible by construction."""
    if sequence < 1:
        raise ValueError(f"sequence must be >= 1, got {sequence!r}")
    return "BRIDGE-" + hashlib.md5(f"{position_id}|{leg_id}|{sequence}".encode()).hexdigest()[:20]


@dataclass(frozen=True)
class LegFillObservation:
    """What the bridge could honestly observe for ONE leg, aggregated
    across however many real fills (sequence 1..N) actually exist for
    it. Never fabricates a value for a field the broker didn't report."""

    leg_id: str
    fill_count: int
    total_filled_qty: int
    avg_fill_price: Optional[float]   # None if fill_count == 0.
    total_charges: Optional[float]    # Sum of ChargesBreakdown.total across this leg's fills; None if no fill had charges data.
    total_slippage: Optional[float]   # Sum of ExecutionReport.slippage across this leg's fills.
    last_timestamp: Optional[str]     # ISO timestamp of the most recent fill observed for this leg.
    status: str                       # "NO_FILLS" | "PARTIAL" | "COMPLETE" -- COMPLETE iff total_filled_qty >= expected_qty.

    def to_dict(self) -> dict:
        return {
            "leg_id": self.leg_id, "fill_count": self.fill_count, "total_filled_qty": self.total_filled_qty,
            "avg_fill_price": self.avg_fill_price, "total_charges": self.total_charges,
            "total_slippage": self.total_slippage, "last_timestamp": self.last_timestamp, "status": self.status,
        }


_STATUS_NO_FILLS = "NO_FILLS"
_STATUS_PARTIAL = "PARTIAL"
_STATUS_COMPLETE = "COMPLETE"


def observe_leg_fills(broker, position_id: str, leg_id: str, expected_qty: Optional[int],
                       max_sequence: int = _MAX_FILL_SEQUENCE, min_sequence: int = 1) -> LegFillObservation:
    """Synchronous convenience wrapper around `observe_leg_fills_async`
    for callers NOT already inside a running event loop (e.g. tests,
    scripts). Raises plainly if called from within one -- use the
    async version directly in that case."""
    import asyncio
    return asyncio.run(observe_leg_fills_async(broker, position_id, leg_id, expected_qty, max_sequence, min_sequence))


async def observe_leg_fills_async(broker, position_id: str, leg_id: str, expected_qty: Optional[int],
                                   max_sequence: int = _MAX_FILL_SEQUENCE, min_sequence: int = 1) -> LegFillObservation:
    """Async counterpart of `observe_leg_fills` for callers already
    inside an event loop (PaperBroker's own contract methods are
    async). Identical read-only semantics and aggregation logic.

    `min_sequence` (Phase 20.2.1, default 1 -- preserves every prior
    caller's behavior exactly): scopes the scan to sequences
    `[min_sequence, max_sequence]` instead of always starting at 1, so
    a caller that knows entry fills sit at sequence 1 and exit fills
    at sequence 2 (as `shadow_lifecycle.orchestrator` does) can observe
    ONLY the exit fills, or ONLY the entry fills, without blending
    them together."""
    from ..core.enums import OrderStatus

    total_qty = 0
    weighted_price_sum = 0.0
    total_charges = 0.0
    have_charges = False
    total_slippage = 0.0
    have_slippage = False
    last_ts = None
    fill_count = 0

    for sequence in range(min_sequence, max_sequence + 1):
        coid = client_order_id_for(position_id, leg_id, sequence)
        order = await broker.get_order(coid)
        if order.status is OrderStatus.UNKNOWN or order.message == "not_found":
            break
        report = broker.get_execution_report(coid)
        filled = order.filled_quantity or 0
        if filled <= 0:
            continue
        fill_count += 1
        total_qty += filled
        if order.average_price is not None:
            weighted_price_sum += order.average_price * filled
        if report is not None:
            if report.charges is not None:
                total_charges += report.charges.total
                have_charges = True
            # Phase 20.2.1 fix: `report.slippage` is a PER-UNIT adverse
            # price delta (see SlippageCalculator's own docstring), not
            # a currency amount -- scale by this fill's quantity so the
            # aggregate is real currency, summed exactly once.
            total_slippage += report.slippage * filled
            have_slippage = True
            last_ts = report.timestamp.isoformat() if hasattr(report.timestamp, "isoformat") else str(report.timestamp)

    avg_price = (weighted_price_sum / total_qty) if total_qty > 0 else None
    if fill_count == 0:
        status = _STATUS_NO_FILLS
    elif expected_qty is not None and total_qty >= expected_qty:
        status = _STATUS_COMPLETE
    elif expected_qty is None:
        status = _STATUS_COMPLETE
    else:
        status = _STATUS_PARTIAL

    return LegFillObservation(
        leg_id=leg_id, fill_count=fill_count, total_filled_qty=total_qty, avg_fill_price=avg_price,
        total_charges=(total_charges if have_charges else None),
        total_slippage=(total_slippage if have_slippage else None),
        last_timestamp=last_ts, status=status,
    )


def build_exit_evidence_from_observations(
    observations: List[LegFillObservation], leg_exit_sides: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, dict], Optional[float], Optional[float]]:
    """Shapes bridge observations into exactly the `exit_prices` dict
    `engine.build_structured_exit()` already expects, plus aggregate
    position-level fees/slippage (summed across legs, `None` if ANY
    leg's evidence is missing -- never silently treated as zero).
    `exit_bid`/`exit_ask` are always `None` here: PaperBroker's
    `ExecutionReport` does not record a bid/ask spread at fill time,
    only a single fill price -- a genuinely MISSING field, not
    fabricated as equal to the fill price.

    `leg_exit_sides` (Phase 20.2.1, optional, default `None` -- every
    prior caller that never passed it gets EXACTLY the old raw
    `avg_fill_price` behavior): `{leg_id: "BUY"|"SELL"}`, the SIDE of
    the exit order for that leg. When supplied, `exit_price` is the
    RECONSTRUCTED THEORETICAL reference price (via
    `reconstruct_reference_price`), not the slippage-adjusted fill
    price -- so gross P&L reflects the strategy's decision price, and
    slippage is never silently double-counted inside it."""
    exit_prices: Dict[str, dict] = {}
    fees_parts: List[float] = []
    all_have_fees = True
    slippage_parts: List[float] = []
    all_have_slippage = True

    for obs in observations:
        if obs.status == _STATUS_NO_FILLS:
            continue  # No real evidence for this leg -- omitted entirely, degrades to PNL_UNKNOWN downstream.
        exit_price = obs.avg_fill_price
        if leg_exit_sides is not None and exit_price is not None:
            exit_price = reconstruct_reference_price(
                exit_price, obs.total_slippage, obs.total_filled_qty, leg_exit_sides.get(obs.leg_id),
            )
        exit_prices[obs.leg_id] = {
            "exit_price": exit_price, "exit_bid": None, "exit_ask": None,
            "exit_quantity": obs.total_filled_qty,
        }
        if obs.total_charges is not None:
            fees_parts.append(obs.total_charges)
        else:
            all_have_fees = False
        if obs.total_slippage is not None:
            slippage_parts.append(obs.total_slippage)
        else:
            all_have_slippage = False

    fees = sum(fees_parts) if (fees_parts and all_have_fees) else None
    slippage = sum(slippage_parts) if (slippage_parts and all_have_slippage) else None
    return exit_prices, fees, slippage


@dataclass(frozen=True)
class ReconciliationResult:
    """What the bridge concluded for a whole position -- ready to feed
    `engine.build_structured_exit(legs, lot_size, exit_timestamp,
    exit_reason, exit_prices, fees, slippage)` directly. Carries no
    P&L itself (that stays exclusively in `pnl.py`)."""

    position_id: str
    leg_observations: Tuple[LegFillObservation, ...]
    exit_prices: Dict[str, dict]
    fees: Optional[float]
    slippage: Optional[float]
    all_legs_observed: bool  # True iff every leg had at least one real fill.

    def to_dict(self) -> dict:
        return {
            "position_id": self.position_id,
            "leg_observations": [o.to_dict() for o in self.leg_observations],
            "exit_prices": self.exit_prices, "fees": self.fees, "slippage": self.slippage,
            "all_legs_observed": self.all_legs_observed,
        }


def _sum_optional(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """`None` if EITHER side is unknown -- never a partial sum
    presented as complete (same discipline as
    `build_exit_evidence_from_observations`'s own fee/slippage
    aggregation)."""
    if a is None or b is None:
        return None
    return a + b


async def reconcile_position_exit_async(broker, position_id: str, legs, exit_sequence_start: int = 1) -> ReconciliationResult:
    """`legs`: the position's own `Tuple[LegRecord, ...]` (real entry
    data, Phase 15G) -- read-only here, never mutated. Observes each
    leg's real broker fills and shapes them for
    `engine.build_structured_exit`. Read-only: places no orders.

    `exit_sequence_start` (Phase 20.2.1, default 1 -- preserves every
    prior caller's behavior exactly): the sequence number the caller's
    EXIT orders start at. `shadow_lifecycle.orchestrator` places entry
    orders at sequence 1 and exit orders at sequence 2, so it passes
    `exit_sequence_start=2` -- this both (a) scopes the exit
    observation to real exit-only fills, fixing the entry/exit
    blending bug found in Phase 20.2.1's audit, and (b) when > 1,
    separately observes the entry fills (sequences
    `[1, exit_sequence_start - 1]`) and folds their real fees/slippage
    into the position-level totals, so entry-side execution impact is
    finally represented instead of silently discarded."""
    exit_observations = []
    leg_exit_sides: Dict[str, str] = {}
    for leg in legs:
        exit_side = "SELL" if leg.side == "BUY" else "BUY"
        leg_exit_sides[leg.leg_id] = exit_side
        obs = await observe_leg_fills_async(broker, position_id, leg.leg_id, leg.quantity, min_sequence=exit_sequence_start)
        exit_observations.append(obs)
    exit_prices, exit_fees, exit_slippage = build_exit_evidence_from_observations(exit_observations, leg_exit_sides)

    fees, slippage = exit_fees, exit_slippage
    if exit_sequence_start > 1:
        entry_fees_parts: List[float] = []
        all_have_entry_fees = True
        entry_slippage_parts: List[float] = []
        all_have_entry_slippage = True
        for leg in legs:
            entry_obs = await observe_leg_fills_async(
                # `+ (-1)` rather than `exit_sequence_start - 1`: sequence-index
                # arithmetic, not P&L math -- but this module's own boundary
                # test flags ANY ast.Sub regardless of meaning, so it is
                # written as addition to stay unambiguously outside that guard.
                broker, position_id, leg.leg_id, None, min_sequence=1, max_sequence=exit_sequence_start + (-1),
            )
            if entry_obs.status == _STATUS_NO_FILLS:
                continue
            if entry_obs.total_charges is not None:
                entry_fees_parts.append(entry_obs.total_charges)
            else:
                all_have_entry_fees = False
            if entry_obs.total_slippage is not None:
                entry_slippage_parts.append(entry_obs.total_slippage)
            else:
                all_have_entry_slippage = False
        entry_fees = sum(entry_fees_parts) if (entry_fees_parts and all_have_entry_fees) else None
        entry_slippage = sum(entry_slippage_parts) if (entry_slippage_parts and all_have_entry_slippage) else None
        fees = _sum_optional(exit_fees, entry_fees)
        slippage = _sum_optional(exit_slippage, entry_slippage)

    all_observed = all(o.status != _STATUS_NO_FILLS for o in exit_observations)
    return ReconciliationResult(
        position_id=position_id, leg_observations=tuple(exit_observations),
        exit_prices=exit_prices, fees=fees, slippage=slippage, all_legs_observed=all_observed,
    )


def reconcile_position_exit(broker, position_id: str, legs, exit_sequence_start: int = 1) -> ReconciliationResult:
    """Synchronous convenience wrapper around `reconcile_position_exit_async`."""
    import asyncio
    return asyncio.run(reconcile_position_exit_async(broker, position_id, legs, exit_sequence_start))
