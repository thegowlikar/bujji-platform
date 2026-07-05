"""MODULE 3 — Execution Engine.

The ONLY module that talks to a broker. It turns broker-agnostic
:class:`OrderRequest` directives into confirmed fills, handling retries,
timeouts, order verification, cancellation, position reconciliation, and
recovery after a restart.

No trading logic lives here. It never decides *whether* to trade — only how to
reliably execute and confirm what it is told to.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..broker.base import Broker
from ..broker.errors import AuthenticationError
from ..core.config import AppConfig
from ..core.enums import OrderStatus, Side
from ..core.logging_setup import log_event
from ..core.models import OptionContract, OrderRequest, OrderResult


class ExecutionError(RuntimeError):
    """Raised when an order cannot be confirmed after all retries."""


class ExecutionEngine:
    """Reliable order placement and confirmation over any :class:`Broker`."""

    def __init__(self, broker: Broker, config: AppConfig, logger: logging.Logger):
        self._broker = broker
        self._cfg = config.broker
        self._log = logger

    async def connect(self) -> None:
        await self._with_retry("connect", self._broker.connect)

    # ------------------------------------------------------------------ #
    # Order lifecycle
    # ------------------------------------------------------------------ #
    async def submit_and_confirm(self, request: OrderRequest) -> OrderResult:
        """Idempotently place an order and confirm its (possibly partial) fill.

        Capital-protection contract:

        * **C3 (no duplicate orders):** the order is placed *at most once* per
          ``client_order_id``. Before placing we query the broker for that id;
          if it already exists (restart or ambiguous retry) we adopt it instead
          of placing again. If ``place_order`` itself raises, we do NOT blindly
          re-place — we first query whether it landed, and only re-place when it
          is confirmed absent.
        * **C4 (partial fills):** the returned :class:`OrderResult` always
          carries a truthful ``filled_quantity``. A partial fill is returned
          (not raised) so the caller can size off what actually executed; only a
          *zero* fill raises :class:`ExecutionError`.
        """
        cid = request.client_order_id
        log_event(
            self._log, "order_submit",
            symbol=request.contract.symbol, side=request.side.value,
            qty=request.quantity, cid=cid,
        )

        # 1. Idempotency: has this exact order already been accepted?
        existing = await self._lookup(cid)
        if existing.status is not OrderStatus.UNKNOWN:
            log_event(self._log, "order_already_present",
                      cid=cid, status=existing.status.value)
            placed = existing
        else:
            placed = await self._place_idempotent(request)

        if placed.status is OrderStatus.REJECTED:
            raise ExecutionError(f"Order rejected: {placed.message}")

        confirmed = await self._await_fill(cid, request.quantity)
        log_event(
            self._log, "order_confirmed",
            cid=cid, status=confirmed.status.value,
            avg_price=confirmed.average_price,
            filled=confirmed.filled_quantity, requested=request.quantity,
        )
        if confirmed.filled_quantity <= 0:
            raise ExecutionError(
                f"Order not filled ({confirmed.status.value}): {confirmed.message}"
            )
        if confirmed.filled_quantity < request.quantity:
            log_event(
                self._log, "order_partial_fill",
                cid=cid, filled=confirmed.filled_quantity,
                requested=request.quantity,
            )
        return confirmed

    async def _place_idempotent(self, request: OrderRequest) -> OrderResult:
        """Place exactly once, verifying-on-error instead of blind retrying."""
        cid = request.client_order_id
        attempts = max(1, self._cfg.retry_attempts)
        for attempt in range(1, attempts + 1):
            try:
                return await self._broker.place_order(request)
            except AuthenticationError:
                # E1/E2: never retry with the same (invalid) credentials — the
                # backoff schedule cannot help; escalate immediately.
                log_event(self._log, "auth_error_detected", call="place_order",
                          cid=cid)
                raise
            except Exception as exc:  # noqa: BLE001 - ambiguous outcome.
                # The order MAY have reached the exchange before the error.
                # Never re-place blindly; query first.
                log_event(self._log, "place_order_error",
                          cid=cid, attempt=attempt, err=str(exc))
                await asyncio.sleep(self._cfg.retry_backoff_seconds * attempt)
                landed = await self._lookup(cid)
                if landed.status is not OrderStatus.UNKNOWN:
                    log_event(self._log, "place_order_landed_despite_error",
                              cid=cid, status=landed.status.value)
                    return landed
                if attempt >= attempts:
                    raise ExecutionError(
                        f"place_order failed and order absent after {attempts} "
                        f"attempts (cid={cid}): {exc}"
                    )
                # Confirmed absent -> safe to try placing again.
        # Unreachable, but keeps the type checker happy.
        raise ExecutionError(f"place_order exhausted (cid={cid})")

    async def _lookup(self, client_order_id: str) -> OrderResult:
        """Query current broker state for a client order id (safe to retry)."""
        try:
            return await self._with_retry(
                "get_order", self._broker.get_order, client_order_id
            )
        except ExecutionError:
            # If we cannot even query, report UNKNOWN so callers stay cautious.
            return OrderResult(client_order_id, OrderStatus.UNKNOWN,
                               message="lookup_failed")

    async def _await_fill(self, client_order_id: str,
                          requested_qty: int) -> OrderResult:
        """Poll until fully filled or terminal; on timeout cancel the remainder.

        Returns the last known result with a truthful ``filled_quantity`` (which
        may be a partial amount). Cancelling the remainder prevents a late fill
        from silently increasing our position after we have moved on (C4).
        """
        deadline = asyncio.get_event_loop().time() + self._cfg.order_timeout_seconds
        last = OrderResult(client_order_id, OrderStatus.UNKNOWN)
        while asyncio.get_event_loop().time() < deadline:
            last = await self._lookup(client_order_id)
            if last.status is OrderStatus.FILLED and \
                    last.filled_quantity >= requested_qty:
                return last
            if last.status in (OrderStatus.REJECTED, OrderStatus.CANCELLED):
                return last
            await asyncio.sleep(self._cfg.poll_interval_seconds)
        # Timed out. Cancel any remaining working quantity so it can't fill late.
        log_event(self._log, "order_timeout",
                  cid=client_order_id, filled=last.filled_quantity,
                  requested=requested_qty)
        await self._safe_cancel(client_order_id)
        return last

    async def _safe_cancel(self, client_order_id: str) -> None:
        try:
            await self._broker.cancel_order(client_order_id)
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup.
            log_event(self._log, "cancel_failed", cid=client_order_id, err=str(exc))

    # ------------------------------------------------------------------ #
    # Market data pass-through (still broker-only concern)
    # ------------------------------------------------------------------ #
    async def get_ltp(self, contract: OptionContract) -> float:
        return await self._with_retry("get_ltp", self._broker.get_ltp, contract)

    async def get_spot(self, underlying: str) -> float:
        return await self._with_retry("get_spot", self._broker.get_spot, underlying)

    # ------------------------------------------------------------------ #
    # Reconciliation & recovery
    # ------------------------------------------------------------------ #
    async def reconcile(self) -> list[dict]:
        """Fetch live broker positions to detect drift after a restart."""
        positions = await self._with_retry(
            "positions", self._broker.get_open_positions
        )
        log_event(self._log, "reconcile", count=len(positions))
        return positions

    # ------------------------------------------------------------------ #
    # Resilience primitive
    # ------------------------------------------------------------------ #
    async def _with_retry(self, name: str, fn, *args):
        """Retry a broker call with exponential backoff on transient errors.

        E1/E2: :class:`AuthenticationError` is deliberately NOT retried and NOT
        wrapped into :class:`ExecutionError` — it is re-raised immediately on
        the first occurrence so callers can distinguish "credentials are
        invalid, a human must act" from "transient failure, retrying may
        help." Burning the retry/backoff schedule on an auth failure only
        delays that distinction for no benefit.
        """
        attempts = self._cfg.retry_attempts
        delay = self._cfg.retry_backoff_seconds
        last_exc: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                return await fn(*args)
            except AuthenticationError as exc:
                log_event(self._log, "auth_error_detected", call=name, err=str(exc))
                raise
            except Exception as exc:  # noqa: BLE001 - broker/network faults.
                last_exc = exc
                log_event(
                    self._log, "broker_call_failed",
                    call=name, attempt=attempt, err=str(exc),
                )
                if attempt < attempts:
                    await asyncio.sleep(delay * attempt)
        raise ExecutionError(f"{name} failed after {attempts} attempts: {last_exc}")
