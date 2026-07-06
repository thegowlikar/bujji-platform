"""FYERS live tick feed — thin wrapper around the official WebSocket client.

Runs on a background OS thread (the SDK's ``connect()`` is blocking) and
exposes a plain, thread-safe "latest price per symbol" store plus connection
health counters. Deliberately does not do anything async-native: callers
(the Tick/Health Engines) poll this store from the asyncio event loop instead
of bridging threads with callbacks, which keeps the concurrency model simple
and matches the already-proven pattern used elsewhere for this exact SDK
(see docs/FYERS_TRANSPORT_READINESS.md's sibling-project reference).

Verified live (see docs/TICK_ENGINE_READINESS.md): `access_token` must be
``"{app_id}:{access_token}"``; `on_message` payloads are
``{"symbol": ..., "ltp": ..., "type": ...}`` for tradable-instrument ticks
(other `type` values are connection/subscription acks, not price ticks, and
are ignored here).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from fyers_apiv3.FyersWebsocket import data_ws


class FyersTickFeed:
    """One WebSocket session; thread-safe last-tick store + health counters."""

    def __init__(self, app_id: str, access_token: str, logger: logging.Logger,
                 log_path: str = "logs") -> None:
        self._app_id = app_id
        self._access_token = access_token
        self._log = logger
        self._log_path = log_path
        self._socket: Optional[data_ws.FyersDataSocket] = None
        self._lock = threading.Lock()
        self._ltp: dict[str, float] = {}
        self._last_tick_at: dict[str, float] = {}
        self._pending_symbols: set[str] = set()
        self._started = False
        self._connected = False
        self._connect_count = 0
        self._last_error: Optional[str] = None
        self._on_connect_hooks: list[Callable[[], None]] = []
        self._on_disconnect_hooks: list[Callable[[], None]] = []

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Idempotent — safe to call multiple times; only connects once."""
        if self._started:
            return
        self._started = True

        def on_connect():
            with self._lock:
                self._connected = True
                self._connect_count += 1
                pending = list(self._pending_symbols)
            self._log.info("tick_feed_connected", extra={
                "data": {"connect_count": self._connect_count}})
            if pending and self._socket is not None:
                self._socket.subscribe(symbols=pending, data_type="SymbolUpdate")
            for hook in self._on_connect_hooks:
                hook()

        def on_message(msg: dict) -> None:
            symbol, ltp = msg.get("symbol"), msg.get("ltp")
            if symbol is None or ltp is None:
                return  # Connection/subscription ack, not a price tick.
            with self._lock:
                self._ltp[symbol] = float(ltp)
                self._last_tick_at[symbol] = time.time()

        def on_error(msg) -> None:
            self._last_error = str(msg)
            self._log.warning("tick_feed_error", extra={"data": {"error": str(msg)}})

        def on_close(msg) -> None:
            with self._lock:
                self._connected = False
            self._log.warning("tick_feed_closed", extra={"data": {"detail": str(msg)}})
            for hook in self._on_disconnect_hooks:
                hook()

        self._socket = data_ws.FyersDataSocket(
            access_token=f"{self._app_id}:{self._access_token}",
            log_path=self._log_path,
            litemode=True,       # Only need LTP, not full market depth.
            write_to_file=False,
            reconnect=True,       # SDK's own reconnect loop (verified live).
            on_connect=on_connect,
            on_close=on_close,
            on_error=on_error,
            on_message=on_message,
        )
        threading.Thread(target=self._socket.connect, daemon=True,
                         name="fyers-tick-feed").start()

    def stop(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close_connection()
            except Exception:  # noqa: BLE001 - best-effort on shutdown.
                pass

    def subscribe(self, symbols: list[str]) -> None:
        with self._lock:
            new = [s for s in symbols if s not in self._pending_symbols]
            self._pending_symbols.update(new)
            connected = self._connected
        if new and connected and self._socket is not None:
            self._socket.subscribe(symbols=new, data_type="SymbolUpdate")

    def on_connect(self, hook: Callable[[], None]) -> None:
        self._on_connect_hooks.append(hook)

    def on_disconnect(self, hook: Callable[[], None]) -> None:
        self._on_disconnect_hooks.append(hook)

    # ------------------------------------------------------------------ #
    # Read-only accessors (safe to call from the asyncio event loop)
    # ------------------------------------------------------------------ #
    def latest(self, symbol: str) -> Optional[float]:
        with self._lock:
            return self._ltp.get(symbol)

    def tick_age_seconds(self, symbol: str) -> Optional[float]:
        with self._lock:
            ts = self._last_tick_at.get(symbol)
        return (time.time() - ts) if ts is not None else None

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def connect_count(self) -> int:
        with self._lock:
            return self._connect_count

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error
