"""Application composition root and async run loop.

Builds every component via dependency injection, starts the dashboard, and runs
the candle loop until the session reaches DONE_FOR_DAY or a shutdown signal is
received. This is the only place the concrete pieces are assembled.
"""
from __future__ import annotations

import asyncio
import logging
import signal as os_signal
from typing import Optional

from .broker.errors import AuthenticationError
from .broker.factory import build_broker
from .broker.fyers_ws import FyersTickFeed
from .core.banner import render_startup_banner
from .core.clock import ClockGuard, now_ist
from .core.config import AppConfig
from .core.event_bus import Event, EventBus, EventType
from .core.logging_setup import log_event, setup_logging
from .core.orchestrator import Orchestrator
from .core.process_lock import LockAcquisitionError, ProcessLock
from .core.runtime_status import RuntimeStatus
from .core.session_state import SessionStore
from .dashboard.server import DashboardServer
from .execution.engine import ExecutionEngine
from .journal.journal import TradeJournal
from .signal.engine import SignalEngine
from .tick.engine import TickEngine
from .tick.health import HealthEngine
from .trade.manager import TradeManager


class Application:
    """Wires modules together and owns the run loop."""

    def __init__(self, config: AppConfig) -> None:
        config.ensure_dirs()
        self._cfg = config
        self._log = setup_logging(config.paths.log_dir, config.log_level)

        # F4: acquire the single-instance lock before anything else touches
        # the broker or shared session state. A duplicate instance must fail
        # here, fast, rather than risk racing another live process.
        self._lock = ProcessLock(config.paths.lock_file)
        try:
            self._lock.acquire()
        except LockAcquisitionError as exc:
            self._log.critical("startup_blocked_duplicate_instance: %s", exc)
            raise
        if not self._lock.enforced:
            self._log.warning(
                "process_lock_not_enforced: this platform lacks flock; "
                "double-instance protection is NOT active"
            )

        self._status = RuntimeStatus()

        broker = build_broker(config, self._log)
        self._exec = ExecutionEngine(broker, config, self._log)
        self._signal = SignalEngine(config, self._log)
        self._trade = TradeManager(config, self._log)
        self._journal = TradeJournal(config.paths.journal_csv, config.paths.database)
        self._store = SessionStore(config.paths.state_file)
        self._bus = EventBus(self._log)
        self._orch = Orchestrator(
            config, self._log, self._signal, self._trade, self._exec,
            self._journal, self._store, self._status, self._bus,
        )

        # Tick/Health Engines: separate from candle-driven Signal Engine by
        # design. Only constructed with a real feed when the broker actually
        # has live tick credentials (fyers/fyers_paper) — plain `paper` mode
        # gets no WebSocket at all, since there's nothing real to subscribe to.
        tick_creds = broker.live_tick_credentials()
        self._tick_feed = (
            FyersTickFeed(tick_creds[0], tick_creds[1], self._log,
                         log_path=str(config.paths.log_dir))
            if tick_creds else None
        )
        self._tick_engine = TickEngine(
            self._orch, self._tick_feed, config, self._log, self._bus, self._status,
        )
        self._health_engine = HealthEngine(
            self._tick_feed, self._orch, self._status, self._log,
        )

        self._wire_event_subscribers()
        self._dashboard = DashboardServer(
            self._status, self._journal, config.dashboard.host,
            config.dashboard.port, config.dashboard.refresh_seconds, self._log,
            stale_after=config.dashboard.stale_after_seconds,
            stop_loss=config.risk.max_mtm_loss,
            profit_target=config.risk.max_mtm_profit,
        )
        # NOTE: asyncio.Event() is intentionally NOT constructed here. On
        # Python < 3.10, an Event binds to whatever loop `get_event_loop()`
        # returns at construction time. `Application(config)` runs as a plain
        # expression BEFORE `asyncio.run()` creates its loop (see `main()`
        # below), so an Event created in `__init__` would bind to a throwaway
        # loop distinct from the one everything actually runs on — surfacing
        # later as "RuntimeError: Future attached to a different loop" the
        # first time it's awaited. It is created in `run()` instead, which is
        # only ever entered while the real loop is already running.
        self._stop: Optional[asyncio.Event] = None
        # D1: detects in-session wall-clock jumps (NTP correction, suspend/
        # resume). Threshold is intentionally conservative and not exposed as
        # a config knob in this pass — 5s of unexplained drift between ticks
        # is already well beyond normal scheduling jitter.
        self._clock_guard = ClockGuard(max_drift_seconds=5.0)

    def _wire_event_subscribers(self) -> None:
        """Attach cross-cutting concerns to the bus without coupling the core.

        This is the seam Version 2 extends (Telegram, voice, AI) — add a
        subscriber here; the trading modules never change.
        """
        def on_event(event: Event) -> None:
            line = f"{event.timestamp.strftime('%H:%M:%S')} {event.type.value}: {event.payload}"
            self._status.push_log(line)
            log_event(self._log, "event", type=event.type.value, **event.payload)

        for et in EventType:
            self._bus.subscribe(et, on_event)

    def _install_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (os_signal.SIGINT, os_signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except NotImplementedError:  # e.g. Windows.
                pass

    async def run(self) -> None:
        # Printed immediately, before anything else can fail — an operator
        # must be able to see at a glance whether this is PAPER or LIVE,
        # never having to infer it from the config file.
        banner = render_startup_banner(self._cfg)
        print(banner)
        self._log.info("\n%s", banner)

        # Created here, not in __init__ — this coroutine only ever executes
        # inside the real running loop (whether entered via `asyncio.run()`
        # or a test's own event loop), so the Event correctly binds to it.
        self._stop = asyncio.Event()
        self._install_signals()
        if self._cfg.dashboard.enabled:
            self._dashboard.start()
        try:
            await self._orch.startup()
        except AuthenticationError as exc:
            # E1/E2: fail fast and unambiguously — an invalid/expired token
            # at startup will not resolve itself. No trading has occurred.
            self._log.critical(
                "startup_blocked_auth_failure: %s. Refresh the broker "
                "access token and restart.", exc,
            )
            self._status.auth_expired = True
            self._status.healthy = False
            self._status.health_detail = f"auth_expired (startup): {exc}"
            raise
        self._health_engine.start()
        self._log.info("Bujji started | broker=%s", self._cfg.broker.name)

        try:
            await self._candle_loop()
        finally:
            await self._shutdown()

    async def _candle_loop(self) -> None:
        """Wait for each boundary; drive candles; guarantee EOD square-off (C2)."""
        minutes = self._cfg.timing.candle_minutes
        while not self._stop.is_set() and self._orch.state.name != "DONE_FOR_DAY":
            await self._sleep_to_next_boundary(minutes)
            if self._stop.is_set():
                break

            # D1: check for an in-session wall-clock jump every tick. Only
            # gates NEW entries (via the orchestrator) — never the EOD
            # square-off below, which must never be blocked by clock distrust.
            drift = self._clock_guard.check()
            if drift.drifted:
                self._log.warning("clock_drift_detected: %s", drift.detail)
            self._orch.set_clock_trust(not drift.drifted, drift.detail)

            now = now_ist().time()
            # EOD enforcement runs on the WALL CLOCK, independent of whether a
            # candle arrives — a stalled/failed feed must never leave a position
            # open past the hard exit (C2).
            if now >= self._cfg.timing.hard_exit:
                try:
                    await self._orch.end_of_day()
                except AuthenticationError as exc:
                    # E1/E2: distinct alert — a token refresh + restart is
                    # needed. Keep looping so a resolved credential issue is
                    # retried on the next boundary rather than abandoning the
                    # EOD square-off attempt entirely.
                    self._log.critical("auth_expired_eod_square_off: %s", exc)
                    self._status.auth_expired = True
                    self._status.healthy = False
                    self._status.health_detail = f"auth_expired (eod): {exc}"
                except Exception:  # noqa: BLE001 - keep retrying next boundary.
                    self._log.exception("eod_square_off_error")
                    self._status.healthy = False
                # If still holding (square-off failed), loop again to retry;
                # otherwise the DONE_FOR_DAY state ends the loop.
                continue

            if not self._within_session():
                continue

            try:
                # NOTE: bypasses ExecutionEngine's retry wrapper (a known,
                # separate architectural wart — see audit item N6); still
                # classified here so an auth failure during candle fetch is
                # never mistaken for a generic/transient error.
                candles = await self._exec._broker.get_recent_candles(  # noqa: SLF001
                    self._cfg.market.underlying, minutes, 1
                )
            except AuthenticationError as exc:
                self._log.critical("auth_expired_candle_fetch: %s", exc)
                self._status.auth_expired = True
                self._status.healthy = False
                self._status.health_detail = f"auth_expired (candle_fetch): {exc}"
                continue
            except Exception:  # noqa: BLE001 - never let the loop die.
                self._log.exception("candle_fetch_error")
                self._status.healthy = False
                continue

            if candles:
                try:
                    await self._orch.on_candle(candles[-1])
                except AuthenticationError as exc:
                    # Defense-in-depth: the orchestrator already handles this
                    # internally on every broker-calling path; this guards
                    # against a future call site that forgets to.
                    self._log.critical("auth_expired_on_candle: %s", exc)
                    self._status.auth_expired = True
                    self._status.healthy = False
                    self._status.health_detail = f"auth_expired (on_candle): {exc}"
                except Exception:  # noqa: BLE001 - never let the loop die.
                    self._log.exception("candle_processing_error")
                    self._status.healthy = False

    def _within_session(self) -> bool:
        now = now_ist().time()
        return self._cfg.timing.orb_start <= now <= self._cfg.timing.trading_end

    async def _sleep_to_next_boundary(self, minutes: int) -> None:
        now = now_ist()
        secs = minutes * 60
        elapsed = (now.minute * 60 + now.second) % secs
        wait = secs - elapsed + 1  # +1s so the candle has surely closed.
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass

    async def _shutdown(self) -> None:
        self._log.info("Shutting down gracefully")
        await self._tick_engine.stop()
        await self._health_engine.stop()
        self._dashboard.stop()
        self._lock.release()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Bujji ORB-VWAP ATM Seller")
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()
    config = AppConfig.load(args.config)
    asyncio.run(Application(config).run())


if __name__ == "__main__":
    main()
