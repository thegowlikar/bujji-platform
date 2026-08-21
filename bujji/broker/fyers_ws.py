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


class _ConnectionHandle:
    """P1-2 fix -- private per-generation identity object.

    Solves a real, ordering problem: the four SDK callbacks
    (`on_connect`/`on_message`/`on_error`/`on_close`) must be fully defined
    *before* `data_ws.FyersDataSocket(...)` is constructed (they are its own
    constructor arguments), so a closure cannot capture "the socket I was
    built for" directly -- that object doesn't exist yet at closure-definition
    time. A handle is created first (`socket=None`), closed over by all four
    callbacks, then its `socket` field is populated immediately after
    construction and BEFORE the background connect thread starts -- so no
    callback can ever be invoked while its own handle's `socket` is still
    unpopulated. A callback's currency is decided by identity
    (`handle is self._current_handle`) against the single live pointer below,
    never by re-reading `self._socket` -- see `FyersTickFeed._connect_locked`.
    """
    __slots__ = ("generation", "socket")

    def __init__(self, generation: int) -> None:
        self.generation = generation
        self.socket: Optional["data_ws.FyersDataSocket"] = None


class FyersTickFeed:
    """One WebSocket session; thread-safe last-tick store + health counters.

    P1-2 fix (docs/CONCURRENCY_LIFETIME_PROOF_P3.md Section 3, deterministically
    proven): a callback belonging to an abandoned socket generation could
    previously fire late and silently corrupt the CURRENT generation's
    connection state (flip `is_connected` back to True, double-count
    `connect_count`, or issue a `subscribe()` call against the wrong socket).
    Fixed via two coordinated mechanisms:

    1. `self._lifecycle_lock` -- a dedicated lock, separate from `self._lock`,
       serializing every step that establishes connection *identity*:
       generation advancement, socket construction/teardown, and assignment
       (`start()`, `force_reconnect()`, `stop()`, and each callback's own
       currency check + lifecycle-state mutation). `self._lock` continues to
       guard only pure tick/error payload data (`_ltp`, `_last_tick_at`,
       `_last_error`) -- the two locks are never held nested in more than one
       fixed order (`_lifecycle_lock` outer, `_lock` inner, only where a
       callback must validate-and-write payload data atomically; see
       `on_message`/`on_error` below), and never held simultaneously anywhere
       else in this file.
    2. `_ConnectionHandle` (above) -- every callback validates its own
       handle's identity against `self._current_handle` as its very first
       action, inside `self._lifecycle_lock`, and returns immediately (a
       complete no-op: no state mutation, no log line, no subscribe call, no
       hook invocation) if it is not the current handle or the feed has been
       stopped. Because the check and every subsequent action happen inside
       one uninterrupted lock hold (or, for `subscribe()`, using a reference
       captured while holding that lock -- never a fresh read of
       `self._socket` performed afterward), a callback that was current at
       the moment of its check cannot be made to act on a socket generation
       that supersedes it mid-flight.
    """

    def __init__(self, app_id: str, access_token: str, logger: logging.Logger,
                 log_path: str = "logs", litemode: bool = True) -> None:
        self._app_id = app_id
        self._access_token = access_token
        self._log = logger
        self._log_path = log_path
        self._litemode = litemode

        # -- Lifecycle identity state: connection generation, the current
        # socket, connection-up/count, pending subscriptions, and the
        # started/stopped flags. All guarded by `_lifecycle_lock`. --------
        self._lifecycle_lock = threading.Lock()
        # Dedicated to serializing the entire force_reconnect() operation
        # (detach -> close -> rebuild) across concurrent callers -- see
        # force_reconnect()'s own docstring for why _lifecycle_lock alone
        # is not enough to prevent two concurrent calls from each
        # rebuilding a socket and leaking one of them.
        self._reconnect_lock = threading.Lock()
        self._socket: Optional[data_ws.FyersDataSocket] = None
        self._generation = 0
        self._current_handle: Optional[_ConnectionHandle] = None
        self._pending_symbols: set[str] = set()
        self._started = False
        self._stopped = False
        self._connected = False
        self._connect_count = 0

        # -- Pure tick/error payload data. Guarded by `_lock`, unrelated to
        # connection identity. -------------------------------------------
        self._lock = threading.Lock()
        self._ltp: dict[str, float] = {}
        self._last_tick_at: dict[str, float] = {}
        self._last_error: Optional[str] = None

        self._on_connect_hooks: list[Callable[[], None]] = []
        self._on_disconnect_hooks: list[Callable[[], None]] = []

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Idempotent — safe to call multiple times; only connects once.
        Refuses (no-ops) after `stop()` -- see `stop()`'s own docstring."""
        with self._lifecycle_lock:
            if self._started or self._stopped:
                return
            self._started = True
            self._connect_locked()

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
        `_connect_locked()`'s own `on_connect` closure runs exactly as it
        does on a normal first connect -- deterministic, not a special case.

        P1-2 fix, corrected: the teardown-advance-rebuild sequence is
        serialized end-to-end by `self._reconnect_lock` (held for the
        whole method, preventing two concurrent calls from each rebuilding
        a socket and leaking one of them), but `close_connection()` itself
        now runs OUTSIDE `_lifecycle_lock`. Holding `_lifecycle_lock`
        across a call into the SDK's `close_connection()` was a real
        deadlock risk: a synchronous `on_close` invoked from inside that
        call would try to re-enter `_lifecycle_lock` (non-reentrant) from
        the same thread and block forever. Instead, the old generation is
        retired and its socket detached atomically under `_lifecycle_lock`
        first (mirroring `stop()`'s own detach-before-close ordering, so a
        synchronous `on_close` firing during the close below sees itself
        as already-stale and no-ops, exactly like any other superseded
        callback -- it must not resurrect state or fire disconnect hooks
        for a teardown this method itself initiated). Only after the close
        completes does a second, separate `_lifecycle_lock` hold advance
        to the new generation. A call arriving after `stop()` is a
        documented, logged no-op -- not an exception -- checked both
        before and after the close (the feed can be stopped concurrently
        while the close is in flight) -- so a late watchdog-triggered
        reconnect can never recreate a socket after shutdown; the one real
        caller (`TickSilenceWatchdog._issue_reconnect`) already wraps this
        call in a broad `try/except`, so an exception would have been
        survivable but would misleadingly log a "failure" for what is an
        expected, benign race against a legitimate shutdown."""
        with self._reconnect_lock:
            with self._lifecycle_lock:
                if self._stopped:
                    self._log.warning("tick_feed_force_reconnect_ignored_after_stop",
                                      extra={"data": {"reason": reason}})
                    return
                self._log.warning("tick_feed_force_reconnect", extra={"data": {"reason": reason}})
                self._connected = False
                self._current_handle = None  # retire now: a synchronous on_close below must see itself as stale
                old_socket = self._socket
                self._socket = None

            if old_socket is not None:
                try:
                    old_socket.close_connection()
                except Exception:  # noqa: BLE001 -- a failed close must never block the fresh reconnect attempt below.
                    pass

            with self._lifecycle_lock:
                if self._stopped:
                    self._log.warning("tick_feed_force_reconnect_ignored_after_stop",
                                      extra={"data": {"reason": reason}})
                    return
                self._connect_locked()

    def stop(self) -> None:
        """P1-2 fix: previously only closed the socket, leaving the
        generation/connection-state untouched -- a delayed callback from
        the just-stopped socket could still fire later and (even under the
        handle-identity fix alone) find itself still "current," silently
        resurrecting `is_connected=True` or issuing a subscribe call on a
        feed the caller explicitly asked to stop.

        Now: `_stopped`, `_connected=False`, and the current handle are all
        invalidated together, atomically, under `_lifecycle_lock`, BEFORE
        the socket is torn down -- so every callback from every generation
        (including the one active at the moment of stopping) finds itself
        non-current from this point forward. `_stopped=True` is an
        independent, explicit, second guard on top of handle invalidation
        (a real handle can never equal the `None` sentinel `_current_handle`
        is set to) -- belt-and-suspenders, not strictly required by the
        handle mechanism alone, kept for legibility and redundancy."""
        with self._lifecycle_lock:
            self._stopped = True
            self._connected = False
            self._current_handle = None
            socket_to_close = self._socket
            self._socket = None
        if socket_to_close is not None:
            try:
                socket_to_close.close_connection()
            except Exception:  # noqa: BLE001 - best-effort on shutdown.
                pass

    def _connect_locked(self) -> None:
        """The one, real place a `data_ws.FyersDataSocket` is constructed
        and started -- used by both `start()` and `force_reconnect()`, so
        both paths run identical, real, tested logic.

        PRECONDITION (P1-2 fix): the caller must already hold
        `self._lifecycle_lock` for the duration of this call. This method
        neither acquires nor releases that lock itself -- it cannot, since
        `threading.Lock` is not reentrant and both real callers already hold
        it when they call this. Defensively re-checks `self._stopped` even
        though both current callers already do, so no internal or future
        caller can bypass the shutdown guard by reaching this method some
        other way."""
        if self._stopped:
            self._log.error("tick_feed_connect_attempted_while_stopped_internal_invariant_violation")
            return

        self._generation += 1
        handle = _ConnectionHandle(self._generation)

        def on_connect():
            with self._lifecycle_lock:
                if self._stopped or handle is not self._current_handle:
                    return  # Stale or stopped: zero mutation, zero log, zero subscribe, zero hooks.
                self._connected = True
                self._connect_count += 1
                pending = list(self._pending_symbols)
                my_socket = handle.socket
                hooks_to_call = list(self._on_connect_hooks)
            # Only reached if this callback was current. Nothing below reads
            # `self._socket` -- `my_socket` is the reference captured above,
            # under the lock, from this callback's own handle.
            self._log.info("tick_feed_connected", extra={
                "data": {"connect_count": self._connect_count}})
            if pending and my_socket is not None:
                my_socket.subscribe(symbols=pending, data_type="SymbolUpdate")
            for hook in hooks_to_call:
                hook()

        def on_message(msg: dict) -> None:
            symbol, ltp = msg.get("symbol"), msg.get("ltp")
            if symbol is None or ltp is None:
                return  # Connection/subscription ack, not a price tick.
            with self._lifecycle_lock:
                if self._stopped or handle is not self._current_handle:
                    return  # Stale or stopped: the tick is dropped, not recorded.
                with self._lock:  # Fixed nested order: _lifecycle_lock outer, _lock inner. Never reversed.
                    self._ltp[symbol] = float(ltp)
                    self._last_tick_at[symbol] = time.time()

        def on_error(msg) -> None:
            with self._lifecycle_lock:
                if self._stopped or handle is not self._current_handle:
                    return  # Stale or stopped: no state mutation, and -- critically -- no log line either.
                with self._lock:  # Same fixed nested order as on_message.
                    self._last_error = str(msg)
            # Only reached if this callback was current.
            self._log.warning("tick_feed_error", extra={"data": {"error": str(msg)}})

        def on_close(msg) -> None:
            with self._lifecycle_lock:
                if self._stopped or handle is not self._current_handle:
                    return  # Stale or already-stopped: zero mutation, zero log, zero hooks.
                self._connected = False
                hooks_to_call = list(self._on_disconnect_hooks)
            self._log.warning("tick_feed_closed", extra={"data": {"detail": str(msg)}})
            for hook in hooks_to_call:
                hook()

        socket = data_ws.FyersDataSocket(
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
        # Populate the handle, then assign, all still under the lock the
        # caller holds -- this is what proves the atomic invariant: after
        # this critical section, self._socket is always exactly the socket
        # built for self._generation. The connect thread (below) is started
        # only after the handle is fully populated -- see _ConnectionHandle's
        # own docstring for why that ordering is what makes population-
        # before-use guaranteed rather than merely likely.
        handle.socket = socket
        self._current_handle = handle
        self._socket = socket
        threading.Thread(target=socket.connect, daemon=True,
                         name="fyers-tick-feed").start()

    def subscribe(self, symbols: list[str]) -> None:
        with self._lifecycle_lock:
            new = [s for s in symbols if s not in self._pending_symbols]
            self._pending_symbols.update(new)
            connected = self._connected
            my_socket = self._socket  # captured under the lock; used below, outside it
        if new and connected and my_socket is not None:
            my_socket.subscribe(symbols=new, data_type="SymbolUpdate")

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
        this class's own `on_connect` closure (in `_connect_locked()`) is
        what deterministically resubscribes every real time, using
        `_pending_symbols`, which this class owns and never lets the SDK
        clear. Subscription restoration is therefore performed
        deterministically BY THIS CLASS, not assumed from the SDK."""
        with self._lifecycle_lock:
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
        with self._lifecycle_lock:
            return self._connected

    @property
    def connect_count(self) -> int:
        with self._lifecycle_lock:
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
