"""Live runtime -> canonical position lifecycle -> outcome memory.

THE MISSING WIRE. Before this module, the one live-capable entrypoint
(`bujji_options_os_runner.py`) reached a real `PaperBroker` fill and a
real exit, then stopped: `PositionLifecycleRuntime.mark_closed()` flipped
an in-memory enum and nothing else. Grepping the entire live path for
`outcome_attribution` / `outcome_memory` returned zero matches. The full
learning chain WAS built and tested -- but against a different lifecycle
package (`bujji.position_lifecycle`), reachable only from
`ShadowLifecycleOrchestrator`, which is never constructed outside tests.
So every real paper trade produced no `OutcomeMemoryRecord` at all, and
Bujji could not learn from a single session.

This module is the adapter that closes that loop, per the Phase 1-3
audit's decision to make `bujji.position_lifecycle` CANONICAL. It builds
nothing new: it reuses `build_position_opened_payload`,
`build_structured_exit`, `build_position_closed_payload`, `apply_event`,
`attribute_position_outcome` and `build_outcome_memory_record`
unmodified, in the same order `ShadowLifecycleOrchestrator` already
proved. It is deliberately NOT a second lifecycle implementation, and it
owns no state -- every function takes the `states` dict in and returns it
out, exactly like the canonical reducer.

TWO REAL IMPEDANCE MISMATCHES, both handled here and nowhere else, which
is precisely why an adapter exists rather than a one-line call:

1. LEG SHAPE. The live runner constructs `msi_trade_construction.StrikeLeg`
   (fields: role/option_type/strike/expiry/delta/premium/side/ratio),
   while the canonical lifecycle reads the `shadow_trade_construction.
   ShadowTradeLeg` surface (`entry_mid`, `entry_bid`, `entry_ask`).
   `_LegView` maps one to the other -- the same duck-typed-view pattern
   `ShadowLifecycleOrchestrator._AttributionView` already established.
   Note `entry_mid` is fed the leg's REAL FILL PRICE (from the broker's
   own `OrderResult.average_price`), never the pre-trade quoted
   `premium`: what a position actually cost is what it filled at, and
   using the quote would silently bias every recorded outcome.

2. ORDER-ID CONVENTION. `position_lifecycle.paper_bridge`'s
   `reconcile_position_exit_async()` can recover exit prices straight
   from the broker, but only for orders placed under its own
   `client_order_id_for(position_id, leg_id, sequence)` scheme. The live
   runner places orders with the Trading Session Governor's own ids, so
   that reconciliation cannot match them. Rather than rename the
   governor's ids (a change to a protected execution path, to serve
   bookkeeping), `close_position()` takes real exit prices from the
   caller. A leg with no supplied exit price degrades honestly to
   `PNL_UNKNOWN` via `build_structured_exit` -- never an assumed price.
   Aligning the two id conventions would let a later phase use
   `reconcile_position_exit_async` directly; that is a deliberate
   follow-up, not a gap this module papers over.

NOTHING HERE DECIDES ANYTHING. No strategy selection, no risk check, no
order placement -- this module never imports a broker and never calls
`place_order`. It records what already happened.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

from bujji.outcome_attribution.engine import attribute_position_outcome
from bujji.outcome_memory.engine import build_outcome_memory_record
from bujji.position_lifecycle.engine import (
    apply_event,
    build_entry_snapshot_for_position,
    build_outcome_attributed_payload,
    build_position_closed_payload,
    build_position_opened_payload,
    build_structured_exit,
)
from bujji.position_lifecycle.models import (
    EVENT_OUTCOME_ATTRIBUTED,
    EVENT_POSITION_CLOSED,
    EVENT_POSITION_OPENED,
    STATUS_CLOSED,
)

TRANSITION_ACCEPTED = "ACCEPTED"


class _LegView:
    """A real `StrikeLeg` presented in the shape the canonical lifecycle
    reads. Carries the leg's REAL fill price as `entry_mid` (see this
    module's docstring on why the quoted premium is not used)."""

    __slots__ = ("role", "option_type", "strike", "expiry", "side", "ratio",
                 "entry_mid", "delta", "entry_bid", "entry_ask")

    def __init__(self, leg: Any, fill_price: Optional[float]) -> None:
        self.role = leg.role
        self.option_type = leg.option_type
        self.strike = leg.strike
        self.expiry = leg.expiry
        self.side = leg.side
        self.ratio = leg.ratio
        # The real fill; falls back to the observed premium ONLY when the
        # broker reported no average price (a rejected/unfilled leg), so
        # this is never silently a quote when a fill exists.
        self.entry_mid = fill_price if fill_price is not None else getattr(leg, "premium", None)
        self.delta = getattr(leg, "delta", None)
        # The live construction path does not carry per-leg bid/ask onto
        # StrikeLeg, so these are honestly None rather than invented.
        self.entry_bid = None
        self.entry_ask = None


class _CandidateView:
    """The live runner's real entry evidence, in the shape
    `build_entry_snapshot_for_position` / `build_position_opened_payload`
    read. Every field is something the runner genuinely has at entry;
    fields it does not have are explicitly None, never fabricated."""

    def __init__(self, *, candidate_id: str, timestamp: str, strategy_family: str,
                 legs: Tuple[_LegView, ...], underlying_symbol: str,
                 underlying_price: Optional[float], lot_size: Optional[int],
                 market_regime: Optional[str], direction: Optional[str],
                 thesis: Optional[str], selection_confidence: Optional[str],
                 source_cycle_id: str) -> None:
        self.candidate_id = candidate_id
        self.timestamp = timestamp
        self.source_cycle_id = source_cycle_id
        self.strategy_family = strategy_family
        self.legs = legs
        self.underlying_symbol = underlying_symbol
        self.underlying_price = underlying_price
        self.lot_size = lot_size
        self.market_regime = market_regime
        self.direction = direction
        self.thesis = thesis
        self.selection_confidence = selection_confidence


def open_position(
    states: Dict[str, Any], session_id: str, *, position_group_id: str, strategy_family: str,
    legs: Any, fill_prices: Sequence[Optional[float]], entry_timestamp: str,
    underlying_symbol: str, underlying_price: Optional[float], lot_size: Optional[int],
    market_regime: Optional[str] = None, direction: Optional[str] = None,
    thesis: Optional[str] = None, selection_confidence: Optional[str] = None,
    source_cycle_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[str], str]:
    """Record a real, already-filled entry as a canonical
    `PositionLifecycle`. Returns `(states, position_id, outcome)`;
    `position_id` is None when the reducer rejected the event, and
    `outcome` always carries the reducer's own verdict so a caller can
    log it rather than assume success.

    `fill_prices`: the broker's own `OrderResult.average_price` per leg,
    POSITIONALLY aligned with `legs`. Deliberately not keyed by
    `leg.role`: real structures reuse a role across legs -- an iron
    condor's four legs are SHORT/SHORT/WING_UPPER/WING_LOWER, so a
    role-keyed mapping would silently drop one short leg's real fill
    price and record the other's in its place. The runner already zips
    legs against order results positionally, which is the same ordering
    relied on here. A `fill_prices` shorter than `legs` (a leg that
    never filled) leaves that leg's price None rather than misaligning
    the rest.
    """
    prices = list(fill_prices)
    leg_views = tuple(
        _LegView(leg, prices[i] if i < len(prices) else None)
        for i, leg in enumerate(legs)
    )
    candidate = _CandidateView(
        candidate_id=position_group_id, timestamp=entry_timestamp,
        source_cycle_id=source_cycle_id or session_id, strategy_family=strategy_family,
        legs=leg_views, underlying_symbol=underlying_symbol, underlying_price=underlying_price,
        lot_size=lot_size, market_regime=market_regime, direction=direction,
        thesis=thesis, selection_confidence=selection_confidence,
    )
    entry = build_entry_snapshot_for_position(candidate, None)
    payload = build_position_opened_payload(session_id, candidate, entry, entry_timestamp)
    states, result = apply_event(states, session_id, EVENT_POSITION_OPENED, session_id, payload)
    if result.outcome != TRANSITION_ACCEPTED:
        return states, None, result.outcome
    return states, payload["position_id"], result.outcome


def map_exit_fills_to_legs(
    lifecycle: Any, exit_symbols: Sequence[str], exit_results: Sequence[Any],
    contracts_by_symbol: Dict[str, Any],
) -> Dict[str, dict]:
    """Turn real exit fills into `build_structured_exit`'s `exit_prices`
    shape, keyed by canonical `leg_id`.

    `exit_symbols` is the broker-position symbol list the executor
    iterated (`positions_for_group(pg_id)`), and `exit_results` is its
    `orders_submitted` -- appended inside that same loop, in that same
    order, so index i of one corresponds to index i of the other
    (`trade_lifecycle_executor.py`). A validation failure mid-loop
    truncates `exit_results` but preserves the prefix, so zipping stays
    correct and the untouched legs simply have no exit price.

    Symbol resolves to a leg on `(strike, option_type, expiry)` rather
    than strike and type alone -- those two collide across expiries in a
    calendar spread, which would attribute one leg's exit price to
    another. A symbol whose contract or leg cannot be resolved is
    SKIPPED, never approximated: `build_structured_exit` then reports
    that leg PNL_UNKNOWN, which is the honest outcome.
    """
    by_key = {
        (float(leg.strike), str(leg.option_type), str(leg.expiry)): leg.leg_id
        for leg in lifecycle.legs
    }
    exit_prices: Dict[str, dict] = {}
    for symbol, result in zip(exit_symbols, exit_results):
        price = getattr(result, "average_price", None)
        if price is None:
            continue  # rejected/unfilled leg -- no real exit evidence.
        contract = contracts_by_symbol.get(symbol)
        if contract is None:
            continue
        leg_id = by_key.get((
            float(contract.strike),
            str(getattr(contract.option_type, "value", contract.option_type)),
            str(contract.expiry),
        ))
        if leg_id is None:
            continue
        exit_prices[leg_id] = {
            "exit_price": price,
            "exit_quantity": getattr(result, "filled_quantity", None),
        }
    return exit_prices


def close_position(
    states: Dict[str, Any], session_id: str, position_id: str, *, exit_timestamp: str,
    exit_reason: str, exit_prices: Dict[str, dict], fees: Optional[float] = None,
    slippage: Optional[float] = None,
) -> Tuple[Dict[str, Any], str]:
    """Record a real exit. `exit_prices` is keyed by `leg_id` exactly as
    `build_structured_exit` expects; any leg absent from it degrades to
    PNL_UNKNOWN rather than assuming a price."""
    lifecycle = states.get(position_id)
    if lifecycle is None:
        return states, "NO_SUCH_POSITION"
    structured_exit = build_structured_exit(
        lifecycle.legs, lifecycle.entry.lot_size, exit_timestamp, exit_reason,
        exit_prices, fees=fees, slippage=slippage,
    )
    payload = build_position_closed_payload(position_id, exit_timestamp, exit_reason, structured_exit)
    states, result = apply_event(states, session_id, EVENT_POSITION_CLOSED, session_id, payload)
    return states, result.outcome


def attribute_and_remember(
    states: Dict[str, Any], session_id: str, position_id: str, *, recorded_at: str,
    portfolio_snapshot: Any = None, valuation_history: Sequence[Optional[float]] = (),
) -> Tuple[Dict[str, Any], Any, str]:
    """The final two hops the live path never reached: attribute the
    closed position's outcome, then write the durable cross-session
    memory record. Returns `(states, outcome_memory_record, outcome)`;
    the record is None whenever attribution was not READY -- an honest
    skip, never a speculative memory."""
    lifecycle = states.get(position_id)
    if lifecycle is None:
        return states, None, "NO_SUCH_POSITION"
    if lifecycle.status != STATUS_CLOSED:
        return states, None, "NOT_CLOSED"

    # `valuation_history`: this position's REAL per-cycle unrealized P&L,
    # one entry per management pass. Without it `compute_mfe_mae` has
    # nothing to work from and MFE/MAE stay None on every record -- which
    # is what happened on every position ever attributed before now,
    # because no caller supplied it. MFE/MAE are the two fields that say
    # how a trade BEHAVED rather than merely how it ended: a winner that
    # was deeply underwater and a winner that never was look identical
    # without them.
    attribution = attribute_position_outcome(lifecycle, valuation_history)
    payload = build_outcome_attributed_payload(position_id, attribution)
    states, result = apply_event(states, session_id, EVENT_OUTCOME_ATTRIBUTED, session_id, payload)
    if result.outcome != TRANSITION_ACCEPTED:
        return states, None, result.outcome

    record = build_outcome_memory_record(states[position_id], attribution, recorded_at, portfolio_snapshot)
    return states, record, TRANSITION_ACCEPTED if record is not None else "ATTRIBUTION_NOT_READY"
