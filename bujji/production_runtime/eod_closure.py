"""EOD closure as a broker-truth state machine.

WHAT THIS REPLACES. `_eod_close()` ran one management pass and then called
`run_market_close_sequence()` -- four lines that transition POSTMARKET then
COMPLETE. Nothing discovered broker positions, nothing cancelled working
orders, and nothing asked the broker whether the account was flat before the
session declared itself COMPLETE and the process exited. A position that the
management pass did not close (rejected exit, timed-out exit, or a position
the in-memory registry could not see) carried overnight with nothing watching
it.

WHAT IS REUSED, NOT REBUILT (the repository's recurring defect is parallel
implementations, so this composes existing proven parts):

  * `place_fn` is the SAME broker-truth placement function entries and exits
    already use -- ExecutionEngine.submit_and_confirm: idempotent placement,
    poll-to-terminal against a deadline, cancel-on-timeout, post-cancel
    reconciliation. This module never calls place_order directly.
  * The reversal pattern (build an OrderRequest from a broker position dict,
    opposite side, symbol-qualified client order id) is lifted from
    `core/orchestrator._flatten_orphan`, which has done exactly this since
    the legacy entrypoint -- correct machinery that production could not
    reach because it belongs to a deprecated runner.
  * Position discovery is `broker.get_open_positions()` UNFILTERED.
    PositionRealityRegistry intersects broker positions with an in-memory
    table of registered symbols, so a position Bujji never registered is
    invisible to it by construction. EOD must see everything.

THE INVARIANT. COMPLETE requires positive proof of flatness. A failed
position read yields UNKNOWN, never FLAT -- "I could not ask" must never
become "there is nothing there".

WORKING ORDERS, HONESTLY SCOPED. The Broker interface exposes
`get_order(client_order_id)` and no enumeration -- there is no
`get_open_orders`. So "working orders" here means THE ORDERS BUJJI ITSELF
SUBMITTED, recovered from the position-group journal, which is authoritative
for that set. An order placed outside Bujji is out of scope and is reported
as such rather than silently assumed absent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

STATE_COMPLETE = "SESSION_COMPLETE"
STATE_UNFLATTENED = "CRITICAL_UNFLATTENED_POSITION"
STATE_BROKER_TRUTH_UNKNOWN = "BROKER_TRUTH_UNKNOWN"

OUTCOME_ALREADY_FLAT = "ALREADY_FLAT"
OUTCOME_FLATTENED = "FLATTENED"
OUTCOME_EXIT_PENDING = "EXIT_PENDING"
OUTCOME_EXIT_PARTIAL = "EXIT_PARTIAL"
OUTCOME_EXIT_REJECTED = "EXIT_REJECTED"
OUTCOME_CANCELLATION_UNKNOWN = "CANCELLATION_UNKNOWN"


@dataclass
class EodClosureResult:
    state: str
    flat: Optional[bool]                      # None == could not establish
    positions_before: Tuple[Dict[str, Any], ...] = ()
    positions_after: Tuple[Dict[str, Any], ...] = ()
    exits_submitted: Tuple[Dict[str, Any], ...] = ()
    # (symbol, OrderResult) for every exit that actually FILLED. Kept as the
    # broker's own result objects, not dicts, because the lifecycle mapper
    # (lifecycle_outcome_bridge.map_exit_fills_to_legs) reads .average_price
    # and .filled_quantity off them -- reusing that one mapper is what keeps
    # this path and the management path from attributing fills differently.
    # Deliberately absent from to_dict(): an OrderResult is not JSON.
    exit_fills: Tuple[Tuple[str, Any], ...] = ()
    # Broker positions no journal group claimed. Flattened, but unaccounted
    # for -- surfaced so a session that closed cleanly still reports that it
    # closed something it never opened.
    orphan_exposure: Tuple[Dict[str, Any], ...] = ()
    cancellations: Tuple[Dict[str, Any], ...] = ()
    attempts: int = 0
    detail: str = ""
    steps: List[str] = field(default_factory=list)

    @property
    def session_closed(self) -> bool:
        """Only a positively verified flat closes a session."""
        return self.state == STATE_COMPLETE and self.flat is True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "flat": self.flat,
            "session_closed": self.session_closed,
            "positions_before": list(self.positions_before),
            "positions_after": list(self.positions_after),
            "exits_submitted": list(self.exits_submitted),
            "cancellations": list(self.cancellations),
            "attempts": self.attempts,
            "detail": self.detail,
            "steps": list(self.steps),
        }


from bujji.broker_truth import for_broker  # noqa: E402
from bujji.production_runtime.exit_lifecycle import (  # noqa: E402
    CAUSE_EOD, ExitJournalUnreadable, ExitPlanRefused, holdings_for_symbols,
    plan as plan_exit, record_ack as record_exit_ack,
    record_rejection as record_exit_rejection,
    record_unknown as record_exit_unknown)


def _utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


# Broker statuses that mean THE ORDER IS FINISHED AND DID NOT SURVIVE. Anything
# not in this set -- including PENDING, TRANSIT, and anything unrecognised --
# is treated as live, because an order whose status we do not understand is not
# an order we may assume is gone.
_TERMINAL_REJECTIONS = frozenset({"REJECTED", "CANCELLED", "CANCELED", "EXPIRED"})

# The broker answered, and its answer was "I do not know". Distinct from a
# rejection: a rejection is terminal and clears the leg for a retry, this
# does not.
_UNKNOWN_STATUSES = frozenset({"UNKNOWN"})


def _record_exit_outcome(journal, exit_plan, leg, outcome, clock, logger) -> None:
    """Write the broker's answer for one exit attempt into the journal.

    THE DEFAULT IS "LIVE", NOT "FAILED". A status this function does not
    recognise records an ACK, so the attempt is treated as an order that
    exists at the venue. The opposite default -- unrecognised means failed --
    would clear the leg for a retry and place a second exit against the same
    position.
    """
    status = getattr(getattr(outcome, "status", None), "value",
                     str(getattr(outcome, "status", None)))
    status_text = str(status or "").upper()
    try:
        if status_text in _TERMINAL_REJECTIONS:
            record_exit_rejection(journal, exit_plan, leg,
                                  f"broker reported {status_text}", clock)
            return
        if status_text in _UNKNOWN_STATUSES:
            record_exit_unknown(journal, exit_plan, leg,
                                f"broker reported {status_text}", clock)
            return
        record_exit_ack(journal, exit_plan, leg,
                        getattr(outcome, "order_id", None), status_text or "UNKNOWN",
                        clock, logger)
    except Exception as exc:  # noqa: BLE001 -- the order is already placed
        # The placement happened; only the record of its fate failed. The
        # attempt's INTENT record still stands, so the leg reads as unresolved
        # and will not be re-sent -- the safe side of this failure.
        logger.critical(
            "EOD: exit %s was PLACED but its outcome could not be journaled "
            "(%s: %s). The attempt is unresolved and will not be re-sent.",
            leg.broker_client_order_id, type(exc).__name__, exc)


def discover_broker_positions(broker, run_async, truth=None
                              ) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """(positions, detail). None means the read FAILED -- never an empty list.

    Collapsing a failed read into `[]` is the single most dangerous
    transformation available here: it reads as "flat" and closes the session.

    M3 (2026-08-22): this rule is unchanged, but it is no longer implemented
    HERE. The same three-valued read was hand-written in three places -- this
    one, `_broker_reports_flat` in the runner, and (wrongly) the registry --
    and three copies of a safety rule is three chances for one of them to
    drift. The parsing now lives in `bujji.broker_truth`; this function is the
    adapter that keeps its (list|None, detail) shape for existing callers.

    The RETURN SHAPE is deliberately preserved: these rows feed order
    construction and valuation, which want the broker's own fields.
    """
    reader = truth if truth is not None else for_broker(broker, run_async=run_async)
    answer = reader.read()
    if answer.is_unknown:
        return None, answer.detail
    live = [dict(leg.raw or leg.as_dict()) for leg in answer.legs]
    return live, f"{len(live)} open leg(s)"


def cancel_our_working_orders(broker, run_async, journal, journal_db_path, logger
                              ) -> List[Dict[str, Any]]:
    """Cancel every order Bujji submitted that the broker still reports as
    working. Returns one record per order acted on.

    SCOPE, stated plainly: the Broker interface has no working-order
    enumeration, so this covers the orders in Bujji's own journal. That set is
    authoritative for what Bujji submitted; an order placed outside Bujji
    cannot be seen from here and is not claimed to be.

    A cancel whose outcome cannot be established is recorded as
    CANCELLATION_UNKNOWN and never as cancelled -- the subsequent position
    re-read is what actually decides whether exposure remains.
    """
    from bujji.core.enums import OrderStatus
    from bujji.production_runtime.execution_journal_bridge import all_group_ids
    from bujji.trading_brain.risk_governor.position_group_fold import fold

    records: List[Dict[str, Any]] = []
    try:
        group_ids = all_group_ids(journal_db_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("EOD: could not enumerate journal groups: %s", exc)
        return records

    for pg_id in group_ids:
        try:
            state = fold(journal.read_events(pg_id))
        except Exception as exc:  # noqa: BLE001
            records.append({"position_group_id": pg_id, "outcome": OUTCOME_CANCELLATION_UNKNOWN,
                            "detail": f"journal unreadable: {exc}"})
            continue
        for coid in state.legs:
            try:
                observed = run_async(broker.get_order(coid))
            except Exception as exc:  # noqa: BLE001
                records.append({"client_order_id": coid, "outcome": OUTCOME_CANCELLATION_UNKNOWN,
                                "detail": f"lookup failed: {type(exc).__name__}: {exc}"})
                continue
            status = getattr(observed, "status", None)
            if status not in (OrderStatus.PENDING, OrderStatus.PARTIAL):
                continue  # already terminal (or unknown to the broker): nothing to cancel
            try:
                result = run_async(broker.cancel_order(coid))
                records.append({"client_order_id": coid,
                                "outcome": getattr(getattr(result, "status", None), "value",
                                                   str(getattr(result, "status", None))),
                                "detail": "cancel requested"})
            except Exception as exc:  # noqa: BLE001
                records.append({"client_order_id": coid, "outcome": OUTCOME_CANCELLATION_UNKNOWN,
                                "detail": f"cancel failed: {type(exc).__name__}: {exc}"})
    return records


def build_flatten_request(position: Dict[str, Any], *, underlying: str, lot_size: int,
                          client_order_id: str):
    """An OrderRequest that reverses exactly this broker position.

    Quantity is the BROKER's reported quantity for THIS symbol -- never a
    remembered figure and never another leg's. Over-reducing a short does not
    stop at zero, it opens the opposite position.

    strike/expiry are placeholders: both brokers route on
    `request.contract.symbol` (verified in FyersBroker.place_order, which
    sends `symbol=request.contract.symbol`), and the broker's position row
    does not carry a parsed strike or expiry to recover honestly. Inventing
    them would be a fabricated field.
    """
    from bujji.core.enums import OptionType, Side
    from bujji.core.models import OptionContract, OrderRequest

    symbol = str(position["symbol"])
    quantity = int(position["qty"])
    side_str = str(position.get("side", "")).upper()
    # Reverse the position. An unknown side is refused rather than guessed:
    # guessing wrong DOUBLES the exposure instead of closing it.
    if side_str == Side.SELL.value:
        exit_side = Side.BUY
    elif side_str == Side.BUY.value:
        exit_side = Side.SELL
    else:
        raise ValueError(f"cannot determine exit side for {symbol!r} from side={side_str!r}")

    option_type = OptionType.CE if symbol.upper().endswith("CE") else OptionType.PE
    contract = OptionContract(symbol=symbol, underlying=underlying, strike=0,
                              option_type=option_type, expiry="", lot_size=lot_size)
    return OrderRequest(contract=contract, side=exit_side, quantity=quantity,
                        client_order_id=client_order_id, limit_price=None,
                        reference_price=position.get("avg_price"), tag="EOD_FLATTEN")


def run_eod_closure(*, broker, place_fn, run_async, journal, journal_db_path,
                    underlying: str, lot_size: int, session_id: str, logger,
                    max_attempts: int = 2, clock=_utc_now) -> EodClosureResult:
    """The closure protocol. COMPLETE only on positively verified flatness.

    IDEMPOTENT. Every attempt re-derives everything from broker truth: it
    cancels Bujji's working orders, re-reads positions, and flattens exactly
    the residual that remains. Running it against an already-flat account
    submits nothing. Running it twice cannot increase exposure, because the
    second run's residual is computed AFTER the first run's fills are visible.
    """
    result = EodClosureResult(state=STATE_BROKER_TRUTH_UNKNOWN, flat=None)
    cancellations: List[Dict[str, Any]] = []
    exits: List[Dict[str, Any]] = []
    # Real fills, carried out so the caller can finalise the LIFECYCLE and not
    # only the book. Flattening without this produced a session that was
    # provably flat at the broker and permanently "NEVER_EXITED" in its own
    # outcome record -- see the runner's _finalize_exit_evidence.
    fills: List[Tuple[str, Any]] = []

    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt

        # 1. CANCEL WORKING ORDERS -- before discovery, so a fill that lands
        #    while the cancel is in flight shows up in the position read below
        #    rather than being missed. A cancel is not a guarantee.
        result.steps.append(f"attempt{attempt}:CANCEL_WORKING_ORDERS")
        cancellations.extend(cancel_our_working_orders(
            broker, run_async, journal, journal_db_path, logger))

        # 2. DISCOVER -- unfiltered broker truth.
        result.steps.append(f"attempt{attempt}:DISCOVER_BROKER_POSITIONS")
        positions, detail = discover_broker_positions(broker, run_async)
        if positions is None:
            result.state = STATE_BROKER_TRUTH_UNKNOWN
            result.flat = None
            result.detail = detail
            result.cancellations = tuple(cancellations)
            result.exits_submitted = tuple(exits)
            result.exit_fills = tuple(fills)
            logger.critical("EOD: broker truth UNKNOWN (%s). Session is NOT complete.", detail)
            return result
        if attempt == 1:
            result.positions_before = tuple(positions)

        # 3. VERIFY FLAT -- the only path to COMPLETE.
        if not positions:
            result.steps.append(f"attempt{attempt}:VERIFY_FLAT")
            result.state = STATE_COMPLETE
            result.flat = True
            result.positions_after = ()
            result.cancellations = tuple(cancellations)
            result.exits_submitted = tuple(exits)
            result.exit_fills = tuple(fills)
            result.detail = ("already flat" if attempt == 1 and not exits
                             else "flat after flattening")
            logger.info("EOD: broker confirms FLAT (%s).", result.detail)
            return result

        # 4/5. RESIDUALS + EXIT INTENTS -- exactly the broker's own quantity,
        #      per symbol, through the canonical broker-truth boundary.
        result.steps.append(f"attempt{attempt}:SUBMIT_EXIT_INTENTS")

        # JOURNAL THE INTENT BEFORE THE PLACEMENT. Until this existed,
        # `place_fn` below was reached with nothing in the journal naming the
        # order: a process that died between the send and the response left an
        # exit order that no later run could find, and the next attempt's
        # residual read could re-send it.
        #
        # Attempts are recorded as history ON THE EXPOSURE GROUP. Nothing is
        # minted here -- an exit is not exposure, and a group per attempt put
        # the order that REDUCES exposure into the margin-active set.
        try:
            holdings, orphans = holdings_for_symbols(
                journal, [(str(pos.get("symbol")), abs(int(pos.get("qty") or 0)))
                          for pos in positions if pos.get("symbol")],
                session_id)
        except ExitJournalUnreadable as exc:
            result.state = STATE_UNFLATTENED
            result.flat = False
            result.detail = f"exposure could not be read from the journal: {exc}"
            result.cancellations = tuple(cancellations)
            result.exits_submitted = tuple(exits)
            result.exit_fills = tuple(fills)
            logger.critical("EOD: REFUSING TO PLACE -- %s.", exc)
            return result

        result.orphan_exposure = tuple(orphans)
        for orphan in orphans:
            # A BROKER POSITION NO JOURNAL GROUP CLAIMS. It IS flattened --
            # leaving naked overnight option exposure is far worse than an exit
            # whose provenance needs explaining -- but its attempt history goes
            # to the SESSION identity, not a minted group, so nothing that
            # measures exposure ever sees a group Bujji did not open.
            logger.critical(
                "EOD: %s x%d is held at the broker and NO journal group claims "
                "it. It is being flattened, and its exit is recorded against "
                "the session rather than a position group. Operator review "
                "required: Bujji has no record of opening this.",
                orphan["symbol"], orphan["quantity"])

        try:
            exit_plan = plan_exit(journal, session_id=session_id, cause=CAUSE_EOD,
                                  holdings=holdings, broker_truth=None,
                                  clock=clock, logger=logger)
        except ExitPlanRefused as exc:
            # Nothing was placed, deliberately. An unjournalable exit is worse
            # than an unattempted one: it is an order nobody can find.
            result.state = STATE_UNFLATTENED
            result.flat = False
            result.detail = f"exit intent could not be journaled: {exc}"
            result.cancellations = tuple(cancellations)
            result.exits_submitted = tuple(exits)
            result.exit_fills = tuple(fills)
            logger.critical(
                "EOD: REFUSING TO PLACE -- %s. The position may still be OPEN. "
                "Operator intervention required.", exc)
            return result

        for refusal in exit_plan.refused:
            exits.append({"symbol": refusal["target_contract_id"],
                          "outcome": "REFUSED_UNRESOLVED_PRIOR_EXIT",
                          "detail": refusal["reason"],
                          "prior_exit_attempt_id": refusal["prior_exit_attempt_id"]})
            logger.critical(
                "EOD: NOT re-sending an exit for %s -- a prior attempt (%s) has "
                "an unresolved fate. Reconcile broker order truth.",
                refusal["target_contract_id"], refusal["prior_exit_attempt_id"])

        by_contract = {leg.target_contract_id: leg for leg in exit_plan.legs}
        for position in positions:
            symbol = str(position.get("symbol"))
            leg = by_contract.get(symbol)
            if leg is None:
                continue  # already exited, refused, or an orphan
            coid = leg.broker_client_order_id
            try:
                request = build_flatten_request(
                    position, underlying=underlying, lot_size=lot_size,
                    client_order_id=coid)
            except Exception as exc:  # noqa: BLE001 -- never guess a side
                exits.append({"symbol": symbol, "outcome": "REFUSED", "detail": str(exc)})
                logger.critical("EOD: refusing to flatten %s -- %s", symbol, exc)
                # The intent is journaled but nothing will ever be sent for it.
                # Recording that terminal fate is what lets a retry proceed;
                # leaving it unresolved would block this leg forever.
                record_exit_rejection(journal, exit_plan, leg,
                                      f"refused before placement: {exc}", clock)
                continue
            try:
                outcome = place_fn(request)
                _record_exit_outcome(journal, exit_plan, leg, outcome, clock, logger)
                fills.append((symbol, outcome))
                exits.append({
                    "symbol": symbol, "client_order_id": coid,
                    "exit_attempt_id": leg.exit_attempt_id,
                    "quantity": int(position["qty"]),
                    "side": getattr(request.side, "value", str(request.side)),
                    "status": getattr(getattr(outcome, "status", None), "value",
                                      str(getattr(outcome, "status", None))),
                    "filled_quantity": getattr(outcome, "filled_quantity", None),
                    # The price this leg actually left at. Absent from the
                    # artifact until now, so a flattened session recorded THAT
                    # it closed but never AT WHAT.
                    "average_price": getattr(outcome, "average_price", None),
                })
            except Exception as exc:  # noqa: BLE001
                exits.append({"symbol": symbol, "client_order_id": coid,
                              "exit_attempt_id": leg.exit_attempt_id,
                              "outcome": "SUBMIT_FAILED", "detail": str(exc)})
                logger.critical("EOD: flatten submit failed for %s: %s", symbol, exc)
                # RECORDED AS UNKNOWN, NOT AS A FAILURE. An exception from the
                # send tells us the CALL failed, not that the ORDER was never
                # received. UNKNOWN is not terminal, so this leg will not be
                # re-sent until broker order truth resolves it -- which is the
                # correct outcome, not a defect.
                try:
                    record_exit_unknown(journal, exit_plan, leg,
                                        f"{type(exc).__name__}: {exc}", clock)
                except Exception as record_exc:  # noqa: BLE001
                    logger.critical(
                        "EOD: could not journal the UNKNOWN fate of %s (%s). The "
                        "leg's INTENT record still blocks a blind re-send.",
                        coid, record_exc)

        # 6. RECONCILE happens as the next attempt's discovery, or below.

    # 7. FINAL RECONCILE after the last attempt.
    result.steps.append("FINAL_RECONCILE")
    positions, detail = discover_broker_positions(broker, run_async)
    result.cancellations = tuple(cancellations)
    result.exits_submitted = tuple(exits)
    result.exit_fills = tuple(fills)
    if positions is None:
        result.state = STATE_BROKER_TRUTH_UNKNOWN
        result.flat = None
        result.detail = detail
        logger.critical("EOD: final broker read UNKNOWN (%s). Session is NOT complete.", detail)
        return result
    result.positions_after = tuple(positions)
    if not positions:
        result.state = STATE_COMPLETE
        result.flat = True
        result.detail = "flat after flattening"
        logger.info("EOD: broker confirms FLAT after %d attempt(s).", result.attempts)
        return result

    result.state = STATE_UNFLATTENED
    result.flat = False
    result.detail = "still open: " + ", ".join(
        f"{p.get('symbol')}x{p.get('qty')}" for p in positions)
    logger.critical(
        "EOD: CRITICAL_UNFLATTENED_POSITION after %d attempt(s) -- %s. The session is "
        "NOT complete and these positions are NOT closed. Operator intervention required.",
        result.attempts, result.detail)
    return result
