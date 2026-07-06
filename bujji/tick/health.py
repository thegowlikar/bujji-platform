"""Health Engine — independent WebSocket/broker connectivity monitoring.

Purely observational: it updates `RuntimeStatus` with connection state, tick
staleness, and reconnect counts, and logs when the feed goes stale. It never
makes a trading decision and never touches the Signal Engine, the Trade
Manager, or the Tick Engine's exit logic — those are separate, independent
modules by design, exactly as specified. Automatic reconnection itself is the
FYERS SDK's own responsibility (`reconnect=True`, verified live); this engine
only surfaces whether it's happening, via the reconnect counter.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..broker.fyers_ws import FyersTickFeed
from ..core.logging_setup import log_event
from ..core.orchestrator import Orchestrator
from ..core.runtime_status import RuntimeStatus

_CHECK_INTERVAL_SECONDS = 5.0
_STALE_THRESHOLD_SECONDS = 15.0


class HealthEngine:
    """Independent connectivity/health monitor — observes only, never decides."""

    def __init__(self, tick_feed: Optional[FyersTickFeed], orchestrator: Orchestrator,
                 status: RuntimeStatus, logger: logging.Logger) -> None:
        self._feed = tick_feed
        self._orch = orchestrator
        self._status = status
        self._log = logger
        self._task: Optional[asyncio.Task] = None
        self._was_connected = False
        self._last_symbol: Optional[str] = None

    def start(self) -> None:
        if self._feed is None:
            return  # No live feed (e.g. plain paper mode) — nothing to watch.
        self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
                self._check_once()
        except asyncio.CancelledError:
            pass

    def _check_once(self) -> None:
        feed = self._feed
        if feed is None:
            return
        connected = feed.is_connected
        self._status.ws_connected = connected
        self._status.ws_connect_count = feed.connect_count

        if connected and not self._was_connected:
            log_event(self._log, "ws_health_connected",
                      connect_count=feed.connect_count)
        elif not connected and self._was_connected:
            log_event(self._log, "ws_health_disconnected",
                      last_error=feed.last_error)
        self._was_connected = connected

        pos = self._orch.position
        if pos is None:
            self._status.ws_last_tick_age_seconds = None
            return

        age = feed.tick_age_seconds(pos.contract.symbol)
        self._status.ws_last_tick_age_seconds = (
            round(age, 1) if age is not None else None
        )
        if age is not None and age > _STALE_THRESHOLD_SECONDS:
            log_event(self._log, "ws_health_stale_tick",
                      symbol=pos.contract.symbol, age_seconds=round(age, 1))
