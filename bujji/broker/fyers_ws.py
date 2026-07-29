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

`litemode` (Sprint 114 Day 1 finding, docs/DAY1_LIVE_SESSION_FINDINGS.md):
defaults to `True`, preserving this class's existing, already-verified
behavior for every current caller. Read directly in the installed
`fyers_apiv3` SDK source (`FyersWebsocket/data_ws.py`): for an INDEX
symbol specifically, lite mode's update path only invokes the message
callback when the raw LTP value differs from the previously stored one
-- the *only* field it compares. Full mode compares every field in the
index payload, including a feed timestamp that changes on nearly every
real broadcast regardless of whether price moved. A live session on
2026-07-28 received exactly one real tick all day for `NSE:NIFTY50-INDEX`
in lite mode -- this parameter exists so an index subscription can opt
out of lite mode without changing behavior for any other symbol/caller,
including the legacy production stack's own existing use of this class.

Production Reliability Sprint P1 (docs/TICK_SILENCE_INCIDENT_P1.md):
2026-07-29's Live Shadow Session #2 received real ticks only twice all
day, both before real market open, then went completely silent for the
remaining ~7.5 real trading hours -- `is_connected` stayed True and
`reconnect_count` stayed 0 the entire time. Root-caused to the
installed `fyers_apiv3` SDK's own internal reconnect path (`connect()`
invokes our `on_connect` hook after a flat `time.sleep(2)`, not after
confirming the real handshake completed; `__on_close` retries
internally and never calls the `on_close` hook we register whenever
`reconnect=True`, which every real caller of this class always passes).
Under a rapid real reconnect burst (5 reconnects in 22 real seconds
during a Cloudflare 502 storm), this SDK-internal state has a real,
demonstrated risk of leaving `self.__ws_object`/the ping thread
referencing a half-open or stale socket with no further error ever
surfacing to us. `force_reconnect()` and `TickSilenceWatchdog` below
exist because the SDK's own reconnect mechanism cannot be trusted to
self-heal or even self-report this failure mode -- detection and
recovery are implemented entirely on our side, external to the SDK's
own (demonstrably unreliable) internal retry loop.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from fyers_apiv3.FyersWebsocket import data_ws

# --- Watchdog state vocabulary. Plain string constants (house
# convention, never enum.Enum). Exactly the 5 states the sprint
# specifies, no more, no fewer. -------------------------------------------
WATCHDOG_HEALTHY = "HEALTHY"
WATCHDOG_TICK_SILENCE = "TICK_SILENCE"
WATCHDOG_RECONNECTING = "RECONNECTING"
WATCHDOG_RECOVERED = "RECOVERED"
WATCHDOG_CRITICAL_FAILURE = "CRITICAL_FAILURE"

ALL_WATCHDOG_STATES = (
    WATCHDOG_HEALTHY, WATCHDOG_TICK_SILENCE, WATCHDOG_RECONNECTING,
    WATCHDOG_RECOVERED, WATCHDOG_CRITICAL_FAILURE,
)


class FyersTickFeed:
    """One WebSocket session; thread-safe last-tick store + health counters."""

    def __init__(self, app_id: str, access_token: str, logger: logging.Logger,
                 log_path: str = "logs", litemode: bool = True) -> None:
        self._app_id = app_id
        self._access_token = access_token
        self._log = logger
        self._log_path = log_path
        self._litemode = litemode
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
        self._connect()

    def force_reconnect(self, reason: str) -> None:
        """Real, external, full teardown-and-recreate reconnect --
        deliberately does NOT rely on the SDK's own internal
        `__on_close`-triggered retry path (Sprint P1 finding: that path
        has a real, demonstrated risk of silently wedging after a rapid
        reconnect burst, with no further error ever surfacing). Tears
        down the current socket, then builds and starts an entirely new
        `data_ws.FyersDataSocket` -- a fresh object, fresh threads, no
        reused internal SDK state. `_pending_symbols` is preserved
        (never cleared), so the real resubscribe-on-connect logic in
        `_connect()`'s own `on_connect` closure runs exactly as it does
        on a normal first connect -- deterministic, not a special case."""
        self._log.warning("tick_feed_force_reconnect", extra={"data": {"reason": reason}})
        with self._lock:
            self._connected = False
        try:
            if self._socket is not None:
                self._socket.close_connection()
        except Exception:  # noqa: BLE001 -- a failed close must never block the fresh reconnect attempt below.
            pass
        self._socket = None
        self._connect()

    def _connect(self) -> None:
        """The one, real place a `data_ws.FyersDataSocket` is
        constructed and started -- used by both `start()` (first
        connect) and `force_reconnect()` (Sprint P1 watchdog-triggered
        reconnect), so both paths run identical, real, tested logic."""
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
            litemode=self._litemode,
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

    @property
    def subscription_state(self) -> str:
        """Real, disclosed operational metric (Sprint P1 Observability
        requirement) -- NOT used by any decision function. 'SUBSCRIBED'
        means real symbols are pending/active resubscription on the next
        (or current) connection; 'NONE' means nothing has ever been
        subscribed. Documented conclusion (Sprint P1 Subscription
        Validation deliverable): the installed SDK does NOT preserve
        subscriptions across its own internal reconnects (`__on_close`'s
        retry path resets `self.symbol_token = {}` before calling
        `self.connect()` again, per the real, installed SDK source) --
        this class's own `on_connect` closure (in `_connect()`) is what
        deterministically resubscribes every real time, using
        `_pending_symbols`, which this class owns and never lets the SDK
        clear. Subscription restoration is therefore performed
        deterministically BY THIS CLASS, not assumed from the SDK."""
        with self._lock:
            return "SUBSCRIBED" if self._pending_symbols else "NONE"

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


class TickSilenceWatchdog:
    """Sprint P1 -- detects 'connected but silent' and forces a real,
    external reconnect. Exists because the installed SDK's own reconnect
    path has a real, demonstrated risk of silently wedging (see this
    module's own docstring and docs/TICK_SILENCE_INCIDENT_P1.md) without
    ever surfacing a further error to us.

    Deliberately a plain, injectable state machine (no direct reference
    to `FyersTickFeed`/the SDK) -- `check()` takes real, caller-supplied
    values and an injected reconnect callable, so this class is fully
    unit-testable without a real socket, real threads, or real time.
    Never touches trading decisions: nothing in this class is imported
    or read by any decision function; it only observes tick freshness
    and, when necessary, calls the one, real, injected reconnect
    function -- exactly the same operation an operator would perform
    manually.
    """

    def __init__(self, *, silence_threshold_seconds: float = 120.0,
                 max_consecutive_failures: int = 3, backoff_base_seconds: float = 30.0,
                 logger: Optional[logging.Logger] = None) -> None:
        self._silence_threshold_seconds = silence_threshold_seconds
        self._max_consecutive_failures = max_consecutive_failures
        self._backoff_base_seconds = backoff_base_seconds
        self._log = logger or logging.getLogger("bujji.broker.tick_silence_watchdog")
        self._state = WATCHDOG_HEALTHY
        self._reconnect_attempt = 0
        self._reconnect_reason: Optional[str] = None
        self._last_reconnect_at_monotonic: Optional[float] = None
        self._current_tick_age: Optional[float] = None
        self._last_tick_timestamp: Optional[float] = None

    # -- Real, disclosed operational metrics (Sprint P1 Observability
    # requirement) -- read-only, never consumed by any decision path. --
    @property
    def watchdog_state(self) -> str:
        return self._state

    @property
    def current_tick_age(self) -> Optional[float]:
        return self._current_tick_age

    @property
    def reconnect_attempt(self) -> int:
        return self._reconnect_attempt

    @property
    def reconnect_reason(self) -> Optional[str]:
        return self._reconnect_reason

    @property
    def last_tick_timestamp(self) -> Optional[float]:
        return self._last_tick_timestamp

    def check(self, *, tick_age: Optional[float], is_connected: bool, market_hours: bool,
              now_monotonic: float, force_reconnect_fn: Callable[[str], None]) -> str:
        """One real, disclosed poll. Returns the new watchdog_state.
        Activates ONLY during real market hours AND while the feed
        reports itself connected (per the sprint's own Watchdog Design
        section) -- a real, visible disconnect is already handled by the
        feed's own `is_connected`/reconnect-count reporting and is out of
        this watchdog's scope."""
        self._current_tick_age = tick_age
        if tick_age is not None:
            self._last_tick_timestamp = now_monotonic - tick_age

        if not market_hours or not is_connected:
            self._state = WATCHDOG_HEALTHY
            self._reconnect_attempt = 0
            self._reconnect_reason = None
            return self._state

        if self._state == WATCHDOG_CRITICAL_FAILURE:
            return self._state  # Fail loudly, never silently resume attempting.

        is_silent = tick_age is not None and tick_age >= self._silence_threshold_seconds

        if not is_silent:
            if self._state in (WATCHDOG_TICK_SILENCE, WATCHDOG_RECONNECTING):
                self._log.info("tick_silence_watchdog_recovered", extra={
                    "data": {"reconnect_attempt": self._reconnect_attempt}})
                self._state = WATCHDOG_RECOVERED
                self._reconnect_attempt = 0
                self._reconnect_reason = None
            else:
                self._state = WATCHDOG_HEALTHY
            return self._state

        # is_silent from here on.
        if self._state in (WATCHDOG_HEALTHY, WATCHDOG_RECOVERED):
            self._state = WATCHDOG_TICK_SILENCE
            self._log.warning("tick_silence_detected", extra={"data": {"tick_age": tick_age}})
            return self._state

        if self._state == WATCHDOG_TICK_SILENCE:
            reason = f"tick_silence age={tick_age:.1f}s >= threshold {self._silence_threshold_seconds:.1f}s"
            self._issue_reconnect(reason, force_reconnect_fn, now_monotonic)
            return self._state

        if self._state == WATCHDOG_RECONNECTING:
            backoff = self._backoff_base_seconds * (2 ** max(0, self._reconnect_attempt - 1))
            elapsed = now_monotonic - (self._last_reconnect_at_monotonic or now_monotonic)
            if elapsed >= backoff:
                if self._reconnect_attempt >= self._max_consecutive_failures:
                    self._state = WATCHDOG_CRITICAL_FAILURE
                    self._log.error("tick_silence_watchdog_critical_failure", extra={"data": {
                        "reconnect_attempt": self._reconnect_attempt, "tick_age": tick_age}})
                    return self._state
                reason = f"tick_silence_persists age={tick_age:.1f}s attempt={self._reconnect_attempt + 1}"
                self._issue_reconnect(reason, force_reconnect_fn, now_monotonic)
            return self._state

        return self._state

    def _issue_reconnect(self, reason: str, force_reconnect_fn: Callable[[str], None], now_monotonic: float) -> None:
        """Exactly ONE reconnect request per call -- the caller (`check`)
        controls cadence via the backoff check above, so this never
        fires more than once per real silence episode without an
        elapsed backoff (Sprint P1's own 'avoid reconnect storms' /
        'single reconnect request per silence event' requirements)."""
        self._reconnect_attempt += 1
        self._reconnect_reason = reason
        self._last_reconnect_at_monotonic = now_monotonic
        self._state = WATCHDOG_RECONNECTING
        try:
            force_reconnect_fn(reason)
        except Exception as exc:  # noqa: BLE001 -- a failed reconnect attempt must never crash the live loop.
            self._log.error("tick_silence_watchdog_reconnect_failed", extra={"data": {"error": str(exc)}}, exc_info=True)
