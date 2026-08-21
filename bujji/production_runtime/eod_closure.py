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


def discover_broker_positions(broker, run_async) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """(positions, detail). None means the read FAILED -- never an empty list.

    Collapsing a failed read into `[]` is the single most dangerous
    transformation available here: it reads as "flat" and closes the session.
    """
    try:
        positions = run_async(broker.get_open_positions())
    except Exception as exc:  # noqa: BLE001
        return None, f"position read failed: {type(exc).__name__}: {exc}"
    if positions is None:
        return None, "broker returned no position list"
    live = []
    for p in positions:
        try:
            qty = int(p.get("qty", 0) or 0)
        except (TypeError, ValueError):
            # A malformed row is not evidence of flatness.
            return None, f"malformed position row: {p!r}"
        if qty > 0:
            live.append(dict(p))
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
                    max_attempts: int = 2) -> EodClosureResult:
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
        for position in positions:
            symbol = str(position.get("symbol"))
            coid = f"EOD-{session_id}-A{attempt}-{symbol}"
            try:
                request = build_flatten_request(
                    position, underlying=underlying, lot_size=lot_size, client_order_id=coid)
            except Exception as exc:  # noqa: BLE001 -- never guess a side
                exits.append({"symbol": symbol, "outcome": "REFUSED", "detail": str(exc)})
                logger.critical("EOD: refusing to flatten %s -- %s", symbol, exc)
                continue
            try:
                outcome = place_fn(request)
                fills.append((symbol, outcome))
                exits.append({
                    "symbol": symbol, "client_order_id": coid,
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
                              "outcome": "SUBMIT_FAILED", "detail": str(exc)})
                logger.critical("EOD: flatten submit failed for %s: %s", symbol, exc)

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
