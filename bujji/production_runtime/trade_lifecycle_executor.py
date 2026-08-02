"""Trade Lifecycle Execution Manager -- BUJJI Options OS v3, Gate F.4.

PURPOSE: convert an already-approved D.4 lifecycle recommendation
(`LifecycleEvaluationResult`, Gate F.3's own output) into a safe
execution action -- reduce, hedge, or record-only. This module makes
NO intelligence decisions: it never decides whether to exit, what
profit target/stop-loss applies, or which instrument hedges a
position. Every action it takes is already fully specified by either
D.4's own action label or an explicit, caller-supplied quantity/
instruction.

STEP 1 OWNERSHIP FINDING (same discipline as F.1/F.3): D.4 answers
"what should happen?" (HOLD/MONITOR/REDUCE_SIZE/ADD_HEDGE/
EXIT_CONSIDERATION/BLOCK_NEW_RISK). This module answers "how do we
execute it safely?" -- nothing more. `exit_engine/engine.py`,
`trade/manager.py::TradeManager`, and `msi_dynamic_management/
engine.py` remain untouched and unimported here, exactly as in every
prior Gate F module -- verified by this module's own test suite.

EXIT_CONSIDERATION IS ADVISORY, NEVER EXECUTED: this gate explicitly
requires "Must NOT immediately exit. This is advisory. Record only."
-- an actual exit order requires an as-yet-unbuilt, explicitly
authorized execution path (a genuine trading decision to close a
position entirely is not something this module manufactures on its
own from an advisory recommendation).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Optional, Tuple

from bujji.core.enums import OrderStatus
from bujji.core.models import OrderResult

from bujji.trading_brain.risk_governor.position_lifecycle_intelligence import (
    ACTION_ADD_HEDGE, ACTION_BLOCK_NEW_RISK, ACTION_EXIT_CONSIDERATION, ACTION_HOLD, ACTION_MONITOR,
    ACTION_REDUCE_SIZE,
)

from .lifecycle_order_builder import IllegalLifecycleOrderError, build_hedge_order, build_reduce_order
from .position_lifecycle_runtime import LifecycleEvaluationResult, PositionLifecycleRuntime
from .position_reality_registry import PositionRealityRegistry

Clock = Callable[[], datetime]

STATUS_NO_ACTION = "NO_ACTION"
STATUS_EXECUTED = "EXECUTED"
STATUS_PARTIAL = "PARTIAL"
STATUS_REJECTED = "REJECTED"
STATUS_FAILED_VALIDATION = "FAILED_VALIDATION"

# NOT part of D.4's own closed action vocabulary (HOLD/MONITOR/REDUCE_SIZE/
# ADD_HEDGE/EXIT_CONSIDERATION/BLOCK_NEW_RISK) -- D.4 never produces this
# label. It is emitted only by the Trading Session Governor's own Exit
# Policy (Gate V1.1) when a hard profit/loss/time limit fires, and is
# semantically distinct from REDUCE_SIZE: a reduce lowers exposure, this
# closes it to zero. Executed via the identical reduce-order codepath
# (a full close is a reduce to zero), but the resulting record is labeled
# honestly for anyone reading the forensic history later.
ACTION_MANDATORY_EXIT = "MANDATORY_EXIT"

_RECORD_ONLY_ACTIONS = (ACTION_HOLD, ACTION_MONITOR, ACTION_EXIT_CONSIDERATION, ACTION_BLOCK_NEW_RISK)


@dataclass(frozen=True)
class LifecycleExecutionResult:
    position_group_id: str
    action: str
    orders_submitted: Tuple[OrderResult, ...]
    status: str
    reason: str


class TradeLifecycleExecutor:
    """Executes exactly the action D.4 already recommended -- never a
    different one, never a bigger one, never a smaller one it invents
    itself. `reduce_quantity`/`hedge_instruction` are the ONLY per-
    call inputs beyond the recommendation itself, both always caller-
    supplied, never fabricated here."""

    def __init__(
        self, broker, registry: PositionRealityRegistry, lifecycle_runtime: PositionLifecycleRuntime,
        event_bus=None,
    ) -> None:
        self._broker = broker
        self._registry = registry
        self._lifecycle_runtime = lifecycle_runtime
        self._event_bus = event_bus

    async def execute(
        self, evaluation: LifecycleEvaluationResult, clock: Clock,
        reduce_quantity: Optional[int] = None, hedge_instruction: Optional[dict] = None,
        reference_prices: Optional[Dict[str, float]] = None,
    ) -> LifecycleExecutionResult:
        """`reference_prices`: optional {symbol: current_market_price},
        e.g. straight from F.3's own Portfolio Reality valuation.
        Passed through to each leg's closing order as the real fill
        basis. Defaults to None -- preserving every existing caller's
        exact prior behavior (closing fill pinned to entry avg_price)
        byte-for-byte -- but any caller managing a real position should
        supply this, or the resulting close will never reflect actual
        market movement no matter how far price has genuinely moved."""
        action = evaluation.recommendation.action
        pg_id = evaluation.position_group_id
        self._publish("LIFECYCLE_ACTION_STARTED", pg_id, action, clock)

        if action in _RECORD_ONLY_ACTIONS:
            self._publish("LIFECYCLE_ACTION_COMPLETED", pg_id, action, clock)
            return LifecycleExecutionResult(
                position_group_id=pg_id, action=action, orders_submitted=(), status=STATUS_NO_ACTION,
                reason=f"{action} is advisory/record-only -- no order submitted.",
            )

        if action in (ACTION_REDUCE_SIZE, ACTION_MANDATORY_EXIT):
            return await self._execute_reduce(pg_id, action, reduce_quantity, clock, reference_prices)

        if action == ACTION_ADD_HEDGE:
            return await self._execute_hedge(pg_id, action, hedge_instruction, clock)

        # Structurally unreachable given D.4's own closed action vocabulary
        # plus the one Session-Governor-owned MANDATORY_EXIT label above,
        # but fails closed rather than silently doing nothing undocumented.
        self._publish("LIFECYCLE_ACTION_FAILED", pg_id, action, clock)
        return LifecycleExecutionResult(
            position_group_id=pg_id, action=action, orders_submitted=(), status=STATUS_FAILED_VALIDATION,
            reason=f"unrecognized action {action!r}",
        )

    async def _execute_reduce(
        self, pg_id: str, action: str, reduce_quantity: Optional[int], clock: Clock,
        reference_prices: Optional[Dict[str, float]] = None,
    ) -> LifecycleExecutionResult:
        if reduce_quantity is None or reduce_quantity <= 0:
            self._publish("LIFECYCLE_ACTION_FAILED", pg_id, action, clock)
            return LifecycleExecutionResult(
                position_group_id=pg_id, action=action, orders_submitted=(), status=STATUS_FAILED_VALIDATION,
                reason=f"{action} recommended but no positive reduce_quantity was supplied -- fail closed, "
                       "never invented here.",
            )

        positions = await self._registry.positions_for_group(pg_id)
        if not positions:
            self._publish("LIFECYCLE_ACTION_FAILED", pg_id, action, clock)
            return LifecycleExecutionResult(
                position_group_id=pg_id, action=action, orders_submitted=(), status=STATUS_REJECTED,
                reason="no open position exists for this group -- nothing to reduce.",
            )

        order_results = []
        for index, position in enumerate(positions):
            symbol = position["symbol"]
            contract = self._registry.contract_for_symbol(pg_id, symbol)
            client_order_id = f"{pg_id}-REDUCE-{clock().isoformat()}-{index}"
            leg_reference_price = reference_prices.get(symbol) if reference_prices else None
            try:
                order_request = build_reduce_order(
                    position, contract, reduce_quantity, client_order_id, reference_price=leg_reference_price,
                )
            except IllegalLifecycleOrderError as exc:
                self._publish("LIFECYCLE_ACTION_FAILED", pg_id, action, clock)
                return LifecycleExecutionResult(
                    position_group_id=pg_id, action=action, orders_submitted=tuple(order_results),
                    status=STATUS_FAILED_VALIDATION, reason=str(exc),
                )
            self._publish("LIFECYCLE_ORDER_CREATED", pg_id, action, clock)
            result = await self._broker.place_order(order_request)
            order_results.append(result)
            self._publish("LIFECYCLE_ORDER_FILLED", pg_id, action, clock)

        status = self._aggregate_status(order_results)
        if status in (STATUS_EXECUTED, STATUS_PARTIAL):
            reality = await self._registry.get_group_reality(pg_id)
            if not reality.is_open:
                self._lifecycle_runtime.mark_closed(pg_id)

        self._publish("LIFECYCLE_ACTION_COMPLETED" if status != STATUS_REJECTED else "LIFECYCLE_ACTION_FAILED",
                       pg_id, action, clock)
        return LifecycleExecutionResult(
            position_group_id=pg_id, action=action, orders_submitted=tuple(order_results), status=status,
            reason=f"reduced by {reduce_quantity} across {len(order_results)} leg(s).",
        )

    async def _execute_hedge(
        self, pg_id: str, action: str, hedge_instruction: Optional[dict], clock: Clock,
    ) -> LifecycleExecutionResult:
        if hedge_instruction is None:
            self._publish("LIFECYCLE_ACTION_COMPLETED", pg_id, action, clock)
            return LifecycleExecutionResult(
                position_group_id=pg_id, action=action, orders_submitted=(), status=STATUS_NO_ACTION,
                reason="ADD_HEDGE recommended but no hedge_instruction was supplied -- recorded as advisory "
                       "only; this module never decides a hedge instrument itself.",
            )

        client_order_id = f"{pg_id}-HEDGE-{clock().isoformat()}"
        try:
            order_request = build_hedge_order(hedge_instruction, client_order_id)
        except IllegalLifecycleOrderError as exc:
            self._publish("LIFECYCLE_ACTION_FAILED", pg_id, action, clock)
            return LifecycleExecutionResult(
                position_group_id=pg_id, action=action, orders_submitted=(), status=STATUS_FAILED_VALIDATION,
                reason=str(exc),
            )

        self._publish("LIFECYCLE_ORDER_CREATED", pg_id, action, clock)
        result = await self._broker.place_order(order_request)
        self._publish("LIFECYCLE_ORDER_FILLED", pg_id, action, clock)
        status = self._aggregate_status([result])
        self._publish("LIFECYCLE_ACTION_COMPLETED" if status != STATUS_REJECTED else "LIFECYCLE_ACTION_FAILED",
                       pg_id, action, clock)
        return LifecycleExecutionResult(
            position_group_id=pg_id, action=action, orders_submitted=(result,), status=status,
            reason="hedge order submitted per caller-supplied instruction.",
        )

    @staticmethod
    def _aggregate_status(order_results) -> str:
        if not order_results:
            return STATUS_REJECTED
        statuses = {r.status for r in order_results}
        if statuses == {OrderStatus.FILLED}:
            return STATUS_EXECUTED
        if OrderStatus.REJECTED in statuses and len(statuses) == 1:
            return STATUS_REJECTED
        if OrderStatus.FILLED in statuses or OrderStatus.PARTIAL in statuses:
            return STATUS_PARTIAL
        return STATUS_REJECTED

    def _publish(self, stage: str, position_group_id: str, action: str, clock: Clock) -> None:
        if self._event_bus is None:
            return
        from bujji.core.event_bus import Event, EventType
        self._event_bus.publish_nowait(Event(
            type=EventType.DECISION_MADE,
            payload={"stage": stage, "position_group_id": position_group_id, "action": action},
            timestamp=clock(),
        ))
