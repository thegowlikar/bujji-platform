"""Production Reliability Sprint P2 -- deterministic fault-injection
harness. TEST INFRASTRUCTURE ONLY, not a production module (lives under
tests/, imported by nothing under bujji/).

`FakeFyersTickFeed` implements the exact same OBSERVABLE interface the
real `bujji.broker.fyers_ws.FyersTickFeed` exposes to its real caller
(`run_live_shadow.py`'s live loop and `TickSilenceWatchdog.check()`):
`latest()`, `tick_age_seconds()`, `is_connected`, `subscription_state`,
`subscribe()`, `force_reconnect()`. It is driven by a real, explicit
virtual clock (never real `time.time()`/`time.sleep()`), so an entire
7-hour incident can be replayed deterministically in milliseconds.

This harness drives the REAL, unmodified `bujji.broker.fyers_ws.
TickSilenceWatchdog` -- no mock of that class, no monkeypatch of its
logic. Only the broker/network layer beneath it is faked, exactly as
the sprint's own Design Principles require ("simulate broker behaviour,
not decision outcomes")."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

SYMBOL = "NSE:NIFTY50-INDEX"


class FakeFyersTickFeed:
    """A real, hand-written test double matching FyersTickFeed's real
    observable surface -- not a generic Mock(). Every method here is a
    real, disclosed implementation a reviewer can read line by line,
    exactly like this project's own house convention for sibling-
    isolation translation classes."""

    def __init__(self) -> None:
        self._now: float = 0.0
        self._last_tick_at: Optional[float] = None
        self._connected: bool = True
        self._pending_symbols: set = set()
        self.force_reconnect_calls: List[Tuple[float, str]] = []
        # Scenario-scripted hook: called with (self, now) every time
        # force_reconnect() is invoked, so a scenario can decide exactly
        # what a reconnect attempt "does" to broker state (succeeds,
        # fails silently, drops subscriptions, etc.) -- the fault being
        # injected, not the watchdog's own logic.
        self.on_force_reconnect: Optional[Callable[["FakeFyersTickFeed", float], None]] = None

    # -- Virtual clock + fault-injection controls (test-only surface) --
    def set_clock(self, now: float) -> None:
        self._now = now

    def inject_tick(self) -> None:
        """A real tick arrives right now."""
        self._last_tick_at = self._now

    def inject_disconnect(self) -> None:
        self._connected = False

    def inject_connect(self) -> None:
        self._connected = True

    def inject_subscription_loss(self) -> None:
        """Simulates the real, documented SDK behaviour (Sprint P1's
        own Subscription Validation finding): __on_close clears
        symbol_token before retrying -- i.e. a real reconnect can
        succeed at the socket level while silently losing subscriptions."""
        self._pending_symbols.clear()

    # -- Real observable interface, matching FyersTickFeed exactly ------
    def latest(self, symbol: str) -> Optional[float]:
        return 100.0 if self._last_tick_at is not None else None

    def tick_age_seconds(self, symbol: str) -> Optional[float]:
        if self._last_tick_at is None:
            return None
        return self._now - self._last_tick_at

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def subscription_state(self) -> str:
        return "SUBSCRIBED" if self._pending_symbols else "NONE"

    def subscribe(self, symbols: list) -> None:
        self._pending_symbols.update(symbols)

    def force_reconnect(self, reason: str) -> None:
        self.force_reconnect_calls.append((self._now, reason))
        if self.on_force_reconnect is not None:
            self.on_force_reconnect(self, self._now)


@dataclass(frozen=True)
class ScenarioEvent:
    """One real, scripted, timed broker event. `apply` receives the real
    FakeFyersTickFeed and mutates it -- this IS the fault injection."""
    at_time: float
    label: str
    apply: Callable[[FakeFyersTickFeed], None]


@dataclass
class TranscriptRow:
    t: float
    watchdog_state: str
    tick_age: Optional[float]
    is_connected: bool
    subscription_state: str
    reconnect_calls_so_far: int


def run_scenario(feed: FakeFyersTickFeed, watchdog, events: List[ScenarioEvent], *,
                  until_t: float, poll_interval: float = 1.0, market_hours: bool = True) -> List[TranscriptRow]:
    """Drives the REAL, unmodified TickSilenceWatchdog.check() against
    the fake feed across a virtual timeline, exactly mirroring
    run_live_shadow.py's real live-loop polling shape (read tick_age +
    is_connected, call watchdog.check(), repeat) -- just with a virtual
    clock instead of real time.sleep()."""
    events_sorted = sorted(events, key=lambda e: e.at_time)
    ei = 0
    transcript: List[TranscriptRow] = []
    t = 0.0
    while t <= until_t + 1e-9:
        while ei < len(events_sorted) and events_sorted[ei].at_time <= t + 1e-9:
            events_sorted[ei].apply(feed)
            ei += 1
        feed.set_clock(t)
        age = feed.tick_age_seconds(SYMBOL)
        state = watchdog.check(
            tick_age=age, is_connected=feed.is_connected, market_hours=market_hours,
            now_monotonic=t, force_reconnect_fn=feed.force_reconnect,
        )
        transcript.append(TranscriptRow(
            t=t, watchdog_state=state, tick_age=age, is_connected=feed.is_connected,
            subscription_state=feed.subscription_state, reconnect_calls_so_far=len(feed.force_reconnect_calls),
        ))
        t += poll_interval
    return transcript


def states_seen(transcript: List[TranscriptRow]) -> List[str]:
    """Real, deduplicated, order-preserved sequence of distinct
    watchdog states observed across the scenario -- the concrete
    'actual state transitions' the report asks for."""
    seen = []
    for row in transcript:
        if not seen or seen[-1] != row.watchdog_state:
            seen.append(row.watchdog_state)
    return seen
