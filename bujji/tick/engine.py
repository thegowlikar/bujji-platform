"""Tick Engine — continuous, tick-driven risk monitoring while IN_POSITION.

This is the risk-management timing change requested: entries remain entirely
candle-driven (the Signal Engine is untouched — see ``bujji/signal/engine.py``
and ``bujji/trade/manager.py``, neither of which this module imports or
calls). Once a position opens, this engine polls the live tick feed for the
option contract's LTP at a fast, fixed cadence, computes MTM continuously, and
triggers an immediate exit the moment a stop-loss or (optional, opt-in)
profit-target threshold is breached — instead of waiting for the next 5-minute
candle close.

It never invents a new exit mechanism: a triggered exit calls
:meth:`Orchestrator.square_off`, the exact same hardened, idempotent,
capital-protected exit path already used for the end-of-day square-off (C1–C4
all apply unchanged). The existing candle-driven risk check in
``TradeManager._check_risk`` also keeps running every candle regardless —
this engine is a strictly faster, additional layer on top of it, never a
replacement, so if the tick feed is down the existing candle-based safety net
still catches a breach (up to 5 minutes later, exactly as it always has).

"Emergency exit" (per the requirement) is this same stop-loss trigger,
described as urgent because it fires on the next tick rather than the next
candle — no separate/new threshold was invented for it.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..broker.fyers_ws import FyersTickFeed
from ..core.config import AppConfig
from ..core.event_bus import Event, EventBus, EventType
from ..core.logging_setup import log_event
from ..core.orchestrator import Orchestrator
from ..core.runtime_status import RuntimeStatus

_POLL_INTERVAL_SECONDS = 1.0


class TickEngine:
    """Subscribes to the open position's option contract and enforces the
    existing risk thresholds continuously instead of once per candle."""

    def __init__(self, orchestrator: Orchestrator, tick_feed: Optional[FyersTickFeed],
                 config: AppConfig, logger: logging.Logger, event_bus: EventBus,
                 status: RuntimeStatus) -> None:
        self._orch = orchestrator
        self._feed = tick_feed
        self._cfg = config
        self._log = logger
        self._status = status
        self._task: Optional[asyncio.Task] = None
        self._symbol: Optional[str] = None

        event_bus.subscribe(EventType.POSITION_OPENED, self._on_position_opened)
        event_bus.subscribe(EventType.POSITION_CLOSED, self._on_position_closed)

    def _on_position_opened(self, event: Event) -> None:
        if self._feed is None:
            log_event(self._log, "tick_engine_no_feed",
                      reason="broker has no live tick credentials")
            return
        self._symbol = event.payload.get("symbol")
        if not self._symbol:
            return
        self._feed.start()
        self._feed.subscribe([self._symbol])
        self._task = asyncio.ensure_future(self._monitor_loop(self._symbol))
        log_event(self._log, "tick_engine_started", symbol=self._symbol)

    def _on_position_closed(self, event: Event) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        self._symbol = None
        self._status.tick_mtm = None
        self._status.tick_last_decision = ""

    async def _monitor_loop(self, symbol: str) -> None:
        try:
            while True:
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                await self._check_once(symbol)
        except asyncio.CancelledError:
            pass  # Normal shutdown on position close — not an error.

    async def _check_once(self, symbol: str) -> None:
        pos = self._orch.position
        if pos is None:
            return  # Closed between the sleep and this check — nothing to do.
        premium = self._feed.latest(symbol) if self._feed else None
        if premium is None:
            return  # No tick yet; the candle-driven check remains the backstop.

        mtm = pos.mtm(premium)
        self._status.tick_mtm = round(mtm, 2)

        stop_loss = -abs(self._cfg.risk.max_mtm_loss)
        take_profit = self._cfg.risk.max_mtm_profit

        reason = None
        if mtm <= stop_loss:
            reason = f"tick_stop_loss(mtm={mtm:.0f},cap={stop_loss:.0f})"
        elif take_profit is not None and mtm >= take_profit:
            reason = f"tick_profit_target(mtm={mtm:.0f},target={take_profit:.0f})"

        if reason is None:
            return

        self._status.tick_last_decision = reason
        log_event(self._log, "tick_exit_triggered", symbol=symbol,
                  mtm=round(mtm, 2), reason=reason)
        await self._orch.square_off(reason)

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self._feed is not None:
            self._feed.stop()
