"""Journaled entry execution -- the order state machine, finally on the path.

WHAT THE AUDIT FOUND (Layer 11, 2026-08-21). A complete, idempotency-keyed
order state machine already existed: MINTED -> CONSTRUCTED -> SUBMIT_INTENT
-> SUBMIT_ACK -> FILL_OBSERVED (cumulative, monotonic, with
NonMonotonicFillReport), CANCEL_*, per-leg crash recovery via
`recover_group()` querying broker truth by client_order_id. Tested,
integrity-checked, transactional (BEGIN IMMEDIATE, unique idempotency keys).

None of it ran. The production entry path (`process_entry_cycle`) called
`place_order` in a bare loop and trusted `is_filled` synchronously; both real
journal databases held ZERO rows; `recover_group` had zero callers. The
composition root even carried `journal` -- handed in by the runner and never
touched. A crash between placements would have left in-flight orders that no
restart could discover, and a partial multi-leg fill left the filled leg
unregistered, unmanaged and un-unwound: the exact one-legged-short that is
the canonical uncontrolled-loss mechanism for an options seller.

THIS MODULE WIRES, IT DOES NOT REBUILD. Every event shape below is copied
from the journal's own test suite and from msi_entry_bridge's already-written
(also-unwired) construction path -- the same discipline as every other repair
this week: the components were correct; they were not connected.

ORDERING GUARANTEES:
  * SUBMIT_INTENT is durably journaled BEFORE place_order is called. If the
    intent cannot be journaled, the order is NOT placed -- an order the
    journal does not know about is exactly the unrecoverable state this
    exists to remove. Fail closed, before money moves.
  * After placement, journal failures are logged CRITICAL but never raise:
    the fill has already happened, and losing the in-memory result too would
    only widen the gap the recovery pass then has to close from broker truth.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from bujji.core.enums import OrderStatus, Side
from bujji.core.models import OrderRequest, OrderResult
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.trading_brain.risk_governor.position_group_fill_reconciliation import compute_fill_delta
from bujji.trading_brain.risk_governor.position_group_fold import (
    LEG_CANCEL_PENDING_UNKNOWN, LEG_SUBMIT_PENDING_UNKNOWN, fold)
from bujji.trading_brain.risk_governor.position_group_mint import mint_position_group_id
from bujji.trading_brain.risk_governor.position_group_recovery import recover_group


def broker_truth_place_fn(execution_engine, run_async, logger):
    """A `place_fn` that returns BROKER TRUTH instead of a synchronous belief.

    WHAT THIS REPLACES. `journaled_entry` was handed
    `lambda o: _await(broker.place_order(o))` and read `is_filled` off the
    immediate response. That is true of PaperBroker and FALSE of any real
    broker, where an order is ACKNOWLEDGED first and fills asynchronously.
    Against FYERS the previous path would read PENDING as "not filled",
    conclude the leg never happened, and leave Bujji short while believing it
    was flat -- the same invisible-position failure as an uncontained partial,
    arriving through the asynchronous door.

    `ExecutionEngine.submit_and_confirm` already solved this and had ZERO
    production callers: idempotent placement (lookup-before-place, and
    verify-don't-re-place on error), poll-to-terminal against a deadline,
    cancel-on-timeout, and -- as of 2026-08-21 -- post-cancel reconciliation
    for a late fill. This adapts it to the sync `place_fn` shape.

    UNKNOWN IS NOT "NOT FILLED". A timeout means Bujji does not yet know. The
    returned OrderResult keeps status UNKNOWN with filled_quantity 0, and the
    CALLER must treat that as unresolved truth -- never as flat. Converting
    the engine's ExecutionError into a synthetic REJECTED here would destroy
    exactly that distinction, so a zero-fill raise is only translated to
    REJECTED when the engine actually confirmed a rejection.
    """
    from bujji.execution.engine import ExecutionError

    def place(request):
        try:
            return run_async(execution_engine.submit_and_confirm(request))
        except ExecutionError as exc:
            message = str(exc)
            # "Order rejected: ..." is the engine's own CONFIRMED-rejection
            # path. Anything else (not filled / not confirmed) is ambiguous
            # and must stay UNKNOWN.
            if message.startswith("Order rejected:"):
                logger.warning("broker CONFIRMED rejection for %s: %s",
                               request.client_order_id, message)
                return OrderResult(request.client_order_id, OrderStatus.REJECTED,
                                   message=message)
            logger.critical(
                "BROKER TRUTH UNKNOWN for %s: %s -- this order may or may not "
                "have filled. Treating as UNKNOWN, never as flat.",
                request.client_order_id, message)
            return OrderResult(request.client_order_id, OrderStatus.UNKNOWN,
                               message=message)
        except Exception as exc:  # noqa: BLE001 -- ambiguity is UNKNOWN, not failure
            logger.critical(
                "BROKER TRUTH UNKNOWN for %s (%s: %s) -- treating as UNKNOWN.",
                request.client_order_id, type(exc).__name__, exc)
            return OrderResult(request.client_order_id, OrderStatus.UNKNOWN,
                               message=f"{type(exc).__name__}: {exc}")

    return place


@dataclass(frozen=True)
class JournaledEntryOutcome:
    position_group_id: Optional[str]
    order_results: Tuple[OrderResult, ...]
    all_filled: bool
    filled: Tuple[Tuple[OrderRequest, OrderResult], ...]
    unfilled: Tuple[Tuple[OrderRequest, OrderResult], ...]
    journal_failures: Tuple[str, ...] = ()
    blocked_reason: Optional[str] = None
    # Legs whose real broker state could not be established. NOT "unfilled":
    # each of these may or may not be a live position, and the only safe
    # reading is that position truth is unknown until reconciled.
    truth_unknown: Tuple[str, ...] = ()

    @property
    def position_truth_known(self) -> bool:
        return not self.truth_unknown


@dataclass(frozen=True)
class ContainmentOutcome:
    """What happened to the filled legs of a partial entry."""
    attempted: bool
    unwound_coids: Tuple[str, ...] = ()
    orphaned_coids: Tuple[str, ...] = ()
    detail: str = ""

    @property
    def clean(self) -> bool:
        return self.attempted and not self.orphaned_coids


def journaled_entry(
    journal: PositionGroupJournal,
    place_fn: Callable[[OrderRequest], OrderResult],
    order_requests: Sequence[OrderRequest],
    *,
    plan_id: str,
    strategy_id: str,
    underlying: str,
    clock,
    logger,
) -> JournaledEntryOutcome:
    """Execute an entry through the journaled state machine.

    `place_fn` is synchronous (the runtime's own `_await(broker.place_order)`
    shape) -- this module adds no event-loop opinions of its own.
    """
    # -- Mint + CONSTRUCTED, before any order exists anywhere. ------------
    try:
        mint = mint_position_group_id(journal, plan_id, strategy_id, underlying, clock=clock)
        pg_id = mint.position_group_id
        contract_map = {f"C{i}": req.client_order_id for i, req in enumerate(order_requests)}
        quantities = {req.client_order_id: req.quantity for req in order_requests}
        actions = {req.client_order_id: getattr(req.side, "value", str(req.side))
                   for req in order_requests}
        journal.append_event(
            pg_id, "CONSTRUCTED", f"{pg_id}:CONSTRUCTED:0",
            {"contract_client_order_map": contract_map,
             "requested_quantities": quantities,
             "actions": actions, "target_position_group_ids": {},
             "target_contract_ids": {}, "flip_link_ids": {}},
            clock=clock,
        )
    except Exception as exc:  # noqa: BLE001 -- cannot journal => must not trade
        logger.error(
            "ENTRY REFUSED -- could not journal the construction (%s: %s). An order "
            "the journal does not know about is unrecoverable after a crash, so "
            "nothing was placed.", type(exc).__name__, exc)
        return JournaledEntryOutcome(
            position_group_id=None, order_results=(), all_filled=False,
            filled=(), unfilled=(), blocked_reason=f"JOURNAL_UNAVAILABLE:{type(exc).__name__}")

    results: List[OrderResult] = []
    filled_pairs: List[Tuple[OrderRequest, OrderResult]] = []
    unfilled_pairs: List[Tuple[OrderRequest, OrderResult]] = []
    journal_failures: List[str] = []
    truth_unknown: List[str] = []

    for request in order_requests:
        coid = request.client_order_id
        # -- INTENT strictly before placement. Fail closed. ---------------
        try:
            journal.append_event(pg_id, "SUBMIT_INTENT", f"{pg_id}:SUBMIT_INTENT:{coid}",
                                 {"client_order_id": coid}, clock=clock)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "SUBMIT_INTENT could not be journaled for %s (%s) -- this leg is NOT "
                "being placed. Any already-filled sibling legs will be contained.",
                coid, exc)
            unfilled_pairs.append((request, OrderResult(
                coid, OrderStatus.UNKNOWN, message=f"not placed: journal failed ({exc})")))
            continue

        result = place_fn(request)
        results.append(result)

        # -- Record what the broker said. Post-placement journal failures
        #    are CRITICAL but never raise: the fill already happened. ------
        try:
            if result.status is OrderStatus.UNKNOWN:
                # No ACK, no FAILURE: both would assert knowledge we do not
                # have. The leg stays SUBMIT_PENDING_UNKNOWN, which is the
                # journal's own word for exactly this condition and is what
                # recovery scans for.
                pass
            elif result.status is OrderStatus.REJECTED:
                journal.append_event(
                    pg_id, "SUBMIT_FAILURE", f"{pg_id}:SUBMIT_FAILURE:{coid}",
                    {"client_order_id": coid,
                     "failure_reason": result.message or "broker rejected",
                     "resolution_basis": "CONFIRMED_REJECTION"},
                    clock=clock)
            else:
                journal.append_event(
                    pg_id, "SUBMIT_ACK", f"{pg_id}:SUBMIT_ACK:{coid}",
                    {"client_order_id": coid,
                     "broker_order_id": result.broker_order_id or coid,
                     "broker_reported_status": getattr(result.status, "value", str(result.status))},
                    clock=clock)
                if result.filled_quantity > 0 and result.average_price is not None:
                    prior = fold(journal.read_events(pg_id)).legs[coid].fill
                    delta = compute_fill_delta(prior, result.filled_quantity, result.average_price)
                    if delta.delta_quantity > 0:
                        journal.append_event(
                            pg_id, "FILL_OBSERVED",
                            f"{pg_id}:FILL_OBSERVED:{coid}:{result.filled_quantity}:{result.average_price}",
                            {"client_order_id": coid,
                             "cumulative_filled_quantity_after": result.filled_quantity,
                             "cumulative_average_fill_price_after": result.average_price,
                             "delta_quantity": delta.delta_quantity,
                             "delta_value": delta.delta_value,
                             "delta_cost_basis_status": delta.cost_basis_status,
                             "fill_price": result.average_price},
                            clock=clock)
        except Exception as exc:  # noqa: BLE001
            journal_failures.append(f"{coid}:{type(exc).__name__}")
            logger.critical(
                "JOURNAL WRITE FAILED AFTER PLACEMENT for %s (%s). The broker state is "
                "real and the journal is now behind it -- the startup recovery pass "
                "will re-derive this leg from broker truth via its SUBMIT_INTENT.",
                coid, exc)

        if result.status is OrderStatus.UNKNOWN:
            # NEITHER filled nor unfilled. Deliberately kept out of both
            # buckets: putting it in `unfilled` would let containment try to
            # unwind a position that may not exist (creating an opposite one),
            # and putting it in `filled` would claim a position that may not
            # exist. The leg also stays SUBMIT_PENDING_UNKNOWN in the journal,
            # so the next startup recovery pass resolves it from broker truth.
            truth_unknown.append(coid)
        else:
            (filled_pairs if result.is_filled else unfilled_pairs).append((request, result))

    return JournaledEntryOutcome(
        position_group_id=pg_id, order_results=tuple(results),
        # An UNKNOWN leg can never make an entry "all filled" -- the whole
        # point is that we do not know what happened to it.
        all_filled=(bool(results) and not unfilled_pairs and not truth_unknown
                    and bool(filled_pairs)),
        filled=tuple(filled_pairs), unfilled=tuple(unfilled_pairs),
        journal_failures=tuple(journal_failures),
        truth_unknown=tuple(truth_unknown))


def contain_partial_entry(
    journal: PositionGroupJournal,
    place_fn: Callable[[OrderRequest], OrderResult],
    outcome: JournaledEntryOutcome,
    *,
    underlying: str,
    strategy_id: str,
    clock,
    logger,
) -> ContainmentOutcome:
    """Unwind the filled legs of a partial entry, through the same machine.

    A partially-filled multi-leg entry is the canonical uncontrolled-loss
    mechanism for an options seller: the filled short leg is naked, and
    before this existed it was also INVISIBLE -- not registered, not managed,
    not unwound. Containment reverses each filled leg at market immediately.

    The unwind is journaled as its own close-group targeting the source
    group -- the journal's own canonical reduction pattern (linked
    FILL_OBSERVED + TARGET_GROUP_REDUCTION_APPLIED), copied from its test
    suite, not invented here.

    A leg whose unwind does not fill is an ORPHAN, reported loudly for the
    caller to register with position management -- never silently dropped:
    an orphan that is at least managed has a stop-loss; an invisible one has
    nothing.
    """
    if not outcome.filled or not outcome.position_group_id:
        return ContainmentOutcome(attempted=False, detail="nothing filled; nothing to contain")

    source_pg = outcome.position_group_id
    unwound: List[str] = []
    orphaned: List[str] = []

    try:
        mint = mint_position_group_id(
            journal, f"{source_pg}-CONTAIN", strategy_id, underlying, clock=clock)
        contain_pg = mint.position_group_id
        contract_map = {f"C{i}": f"{req.client_order_id}-UNWIND"
                        for i, (req, _res) in enumerate(outcome.filled)}
        journal.append_event(
            contain_pg, "CONSTRUCTED", f"{contain_pg}:CONSTRUCTED:0",
            {"contract_client_order_map": contract_map,
             "requested_quantities": {f"{req.client_order_id}-UNWIND": res.filled_quantity
                                      for req, res in outcome.filled},
             "actions": {},
             "target_position_group_ids": {f"{req.client_order_id}-UNWIND": source_pg
                                           for req, _res in outcome.filled},
             "target_contract_ids": {f"{req.client_order_id}-UNWIND": f"C{i}"
                                     for i, (req, _res) in enumerate(outcome.filled)},
             "flip_link_ids": {}},
            clock=clock)
    except Exception as exc:  # noqa: BLE001 -- containment journaling failed; still unwind
        logger.critical("containment group could not be journaled (%s) -- unwinding "
                        "WITHOUT journal coverage rather than leaving naked legs.", exc)
        contain_pg = None

    from bujji.journal.position_group_journal import EventSpec

    for i, (request, fill_result) in enumerate(outcome.filled):
        coid = request.client_order_id
        unwind_coid = f"{coid}-UNWIND"
        reverse_side = Side.BUY if request.side is Side.SELL else Side.SELL
        unwind_request = OrderRequest(
            contract=request.contract, side=reverse_side,
            quantity=fill_result.filled_quantity, client_order_id=unwind_coid,
            limit_price=None, reference_price=fill_result.average_price,
            tag=f"PARTIAL_ENTRY_CONTAINMENT:{source_pg}",
        )
        try:
            if contain_pg is not None:
                journal.append_event(contain_pg, "SUBMIT_INTENT",
                                     f"{contain_pg}:SUBMIT_INTENT:{unwind_coid}",
                                     {"client_order_id": unwind_coid}, clock=clock)
            result = place_fn(unwind_request)
            if contain_pg is not None and result.status is not OrderStatus.REJECTED:
                journal.append_event(
                    contain_pg, "SUBMIT_ACK", f"{contain_pg}:SUBMIT_ACK:{unwind_coid}",
                    {"client_order_id": unwind_coid,
                     "broker_order_id": result.broker_order_id or unwind_coid,
                     "broker_reported_status": getattr(result.status, "value", str(result.status))},
                    clock=clock)
            if result.is_filled and result.average_price is not None:
                if contain_pg is not None:
                    prior = fold(journal.read_events(contain_pg)).legs[unwind_coid].fill
                    delta = compute_fill_delta(prior, result.filled_quantity, result.average_price)
                    fill_spec = EventSpec(
                        contain_pg, "FILL_OBSERVED",
                        f"{contain_pg}:FILL_OBSERVED:{unwind_coid}:{result.filled_quantity}:{result.average_price}",
                        {"client_order_id": unwind_coid,
                         "cumulative_filled_quantity_after": result.filled_quantity,
                         "cumulative_average_fill_price_after": result.average_price,
                         "delta_quantity": delta.delta_quantity,
                         "delta_value": delta.delta_value,
                         "delta_cost_basis_status": delta.cost_basis_status,
                         "fill_price": result.average_price})
                    reduction_spec = EventSpec(
                        source_pg, "TARGET_GROUP_REDUCTION_APPLIED",
                        f"{source_pg}:TARGET_GROUP_REDUCTION_APPLIED:{unwind_coid}",
                        {"source_client_order_id": unwind_coid,
                         "source_position_group_id": contain_pg,
                         "target_contract_id": f"C{i}",
                         "reduced_quantity_delta": result.filled_quantity})
                    journal.append_linked_events([fill_spec, reduction_spec], clock=clock)
                unwound.append(coid)
                logger.warning("PARTIAL ENTRY CONTAINED -- %s unwound at %.2f (qty %d).",
                               coid, result.average_price, result.filled_quantity)
            else:
                orphaned.append(coid)
                logger.critical(
                    "PARTIAL ENTRY ORPHAN -- unwind of %s did NOT fill (status=%s). This "
                    "leg is a live position and MUST be registered for management.",
                    coid, result.status)
        except Exception as exc:  # noqa: BLE001 -- one leg's failure must not abandon the rest
            orphaned.append(coid)
            logger.critical("PARTIAL ENTRY ORPHAN -- unwind of %s raised %s: %s. This leg "
                            "is a live position and MUST be registered for management.",
                            coid, type(exc).__name__, exc)

    return ContainmentOutcome(
        attempted=True, unwound_coids=tuple(unwound), orphaned_coids=tuple(orphaned),
        detail=f"unwound={len(unwound)} orphaned={len(orphaned)} of {len(outcome.filled)} filled leg(s)")


# --------------------------------------------------------------------------- #
# Startup recovery
# --------------------------------------------------------------------------- #

def all_group_ids(journal_db_path: str) -> List[str]:
    """Every position_group_id the journal has ever seen. Read-only; the
    journal class itself deliberately exposes only per-group reads, and
    startup recovery is the one caller that must enumerate."""
    try:
        conn = sqlite3.connect(journal_db_path)
        try:
            return [r[0] for r in conn.execute(
                "SELECT DISTINCT position_group_id FROM position_group_events")]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


def unresolved_group_ids(journal: PositionGroupJournal, journal_db_path: str) -> List[str]:
    """Groups with a leg still SUBMIT_PENDING_UNKNOWN / CANCEL_PENDING_UNKNOWN
    -- the exact states a crash mid-placement leaves behind."""
    pending = []
    for pg_id in all_group_ids(journal_db_path):
        try:
            state = fold(journal.read_events(pg_id))
        except Exception:  # noqa: BLE001 -- an unreadable group IS unresolved
            pending.append(pg_id)
            continue
        if any(leg.submit_status in (LEG_SUBMIT_PENDING_UNKNOWN, LEG_CANCEL_PENDING_UNKNOWN)
               for leg in state.legs.values()):
            pending.append(pg_id)
    return pending


class _SyncBrokerOrderLookup:
    """Adapts the broker's async get_order to recover_group's sync protocol.
    found=False exactly when the broker reports UNKNOWN/not_found."""

    def __init__(self, broker, run_async) -> None:
        self._broker = broker
        self._run_async = run_async

    def get_order(self, client_order_id: str):
        result = self._run_async(self._broker.get_order(client_order_id))

        class _Lookup:
            pass

        lookup = _Lookup()
        lookup.client_order_id = client_order_id
        lookup.status = getattr(result.status, "value", str(result.status))
        lookup.filled_quantity = result.filled_quantity
        lookup.average_price = result.average_price
        lookup.found = result.status is not OrderStatus.UNKNOWN
        return lookup


def recover_unresolved_at_startup(
    journal: PositionGroupJournal, journal_db_path: str, broker, run_async, logger, clock,
) -> Dict[str, Any]:
    """Run once at process start, before any new mint -- the recovery
    module's own stated contract, honoured for the first time.

    Returns a summary the runner records. Never raises: a recovery pass
    that cannot complete leaves the pending groups pending, which the
    summary reports honestly -- and the caller decides whether trading may
    proceed (fail closed on unresolved legs is the runner's call to make,
    loudly, not this helper's to bury).
    """
    summary: Dict[str, Any] = {"groups_checked": 0, "groups_recovered": 0,
                               "legs_resolved": 0, "unresolved_after": [], "errors": []}
    try:
        pending = unresolved_group_ids(journal, journal_db_path)
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(f"enumeration failed: {exc}")
        return summary

    # DO NOT MANUFACTURE CERTAINTY FROM A BROKER THAT FORGETS (2026-08-21).
    #
    # recover_group resolves a pending leg by calling broker.get_order(coid),
    # and writes SUBMIT_FAILURE with resolution_basis
    # RECOVERY_CONFIRMED_NEVER_RECEIVED when the broker reports not-found.
    # That is sound against an exchange-backed book. It is FALSE against a
    # broker whose order book is in-process memory: the journal survives a
    # crash, PaperBroker's `_orders` dict does not, and _production_paper_broker
    # builds a fresh empty one every session. Every crash-left leg would
    # therefore look "never received" and be durably recorded as such -- an
    # authoritative-looking claim about the exchange derived from this
    # process's amnesia.
    #
    # When the broker cannot vouch for pre-restart orders, the legs stay
    # UNRESOLVED. The caller already fails closed on that, which is the
    # correct outcome: we genuinely do not know.
    if not getattr(broker, "order_book_survives_restart", False):
        summary["unresolved_after"] = list(pending)
        summary["skipped_reason"] = (
            f"{type(broker).__name__} does not persist its order book across a "
            "restart, so a not-found from it is this process's amnesia, not "
            "evidence about the exchange. Legs left UNRESOLVED rather than "
            "recorded as never-received.")
        if pending:
            logger.critical(
                "STARTUP RECOVERY SKIPPED -- %s cannot vouch for pre-restart orders. "
                "%d group(s) remain UNRESOLVED: %s. This is UNKNOWN, not clean.",
                type(broker).__name__, len(pending), pending)
        return summary

    lookup = _SyncBrokerOrderLookup(broker, run_async)
    for pg_id in pending:
        summary["groups_checked"] += 1
        try:
            outcomes = recover_group(journal, lookup, pg_id, clock=clock)
            resolved = sum(1 for o in outcomes if o.resolved)
            summary["legs_resolved"] += resolved
            if resolved:
                summary["groups_recovered"] += 1
            for o in outcomes:
                logger.warning("STARTUP RECOVERY -- %s/%s: %s (resolved=%s)",
                               pg_id, o.client_order_id, o.detail, o.resolved)
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"{pg_id}: {type(exc).__name__}: {exc}")
            logger.error("STARTUP RECOVERY failed for %s: %s", pg_id, exc)

    summary["unresolved_after"] = unresolved_group_ids(journal, journal_db_path)
    return summary
