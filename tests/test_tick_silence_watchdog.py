"""Tests for bujji.broker.fyers_ws's Sprint P1 TickSilenceWatchdog and
FyersTickFeed.force_reconnect/subscription_state.

Every test drives the watchdog with real, caller-supplied values and an
injected reconnect callable -- no real socket, no real thread, no real
time.sleep anywhere in this file, per the class's own design (fully
unit-testable in isolation)."""
from __future__ import annotations

import logging

import pytest

from bujji.broker.fyers_ws import (
    FyersTickFeed, TickSilenceWatchdog,
    WATCHDOG_HEALTHY, WATCHDOG_TICK_SILENCE, WATCHDOG_RECONNECTING,
    WATCHDOG_RECOVERED, WATCHDOG_CRITICAL_FAILURE,
)

LOG = logging.getLogger("test")


def _wd(**kwargs):
    defaults = dict(silence_threshold_seconds=100.0, max_consecutive_failures=3,
                     backoff_base_seconds=10.0, logger=LOG)
    defaults.update(kwargs)
    return TickSilenceWatchdog(**defaults)


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, reason: str) -> None:
        self.calls.append(reason)


# --- Normal market flow ---

def test_normal_flow_stays_healthy_never_reconnects():
    wd = _wd()
    r = _Recorder()
    for t in range(0, 500, 50):
        state = wd.check(tick_age=5.0, is_connected=True, market_hours=True, now_monotonic=float(t), force_reconnect_fn=r)
        assert state == WATCHDOG_HEALTHY
    assert r.calls == []


# --- Temporary silence (recovers before threshold matters) ---

def test_temporary_silence_under_threshold_stays_healthy():
    wd = _wd()
    r = _Recorder()
    assert wd.check(tick_age=50.0, is_connected=True, market_hours=True, now_monotonic=0.0, force_reconnect_fn=r) == WATCHDOG_HEALTHY
    assert r.calls == []


# --- Prolonged silence -> TICK_SILENCE -> RECONNECTING ---

def test_prolonged_silence_transitions_through_tick_silence_to_reconnecting():
    wd = _wd()
    r = _Recorder()
    assert wd.check(tick_age=150.0, is_connected=True, market_hours=True, now_monotonic=0.0, force_reconnect_fn=r) == WATCHDOG_TICK_SILENCE
    assert r.calls == []  # no reconnect issued on the FIRST silent observation
    assert wd.check(tick_age=151.0, is_connected=True, market_hours=True, now_monotonic=1.0, force_reconnect_fn=r) == WATCHDOG_RECONNECTING
    assert len(r.calls) == 1  # exactly one reconnect issued once silence is confirmed


# --- Successful recovery ---

def test_successful_recovery_after_reconnect():
    wd = _wd()
    r = _Recorder()
    wd.check(tick_age=150.0, is_connected=True, market_hours=True, now_monotonic=0.0, force_reconnect_fn=r)
    wd.check(tick_age=151.0, is_connected=True, market_hours=True, now_monotonic=1.0, force_reconnect_fn=r)
    assert wd.check(tick_age=2.0, is_connected=True, market_hours=True, now_monotonic=2.0, force_reconnect_fn=r) == WATCHDOG_RECOVERED
    assert wd.check(tick_age=3.0, is_connected=True, market_hours=True, now_monotonic=3.0, force_reconnect_fn=r) == WATCHDOG_HEALTHY
    assert wd.reconnect_attempt == 0  # reset on recovery


# --- Repeated silence -> exponential backoff, no reconnect storm ---

def test_repeated_silence_respects_exponential_backoff_no_storm():
    wd = _wd()
    r = _Recorder()
    wd.check(tick_age=150.0, is_connected=True, market_hours=True, now_monotonic=0.0, force_reconnect_fn=r)
    wd.check(tick_age=151.0, is_connected=True, market_hours=True, now_monotonic=1.0, force_reconnect_fn=r)  # attempt 1
    assert len(r.calls) == 1
    # backoff after attempt 1 = 10 * 2^0 = 10s -- polling every 1s for 5s must NOT trigger another reconnect.
    for t in range(2, 7):
        wd.check(tick_age=151.0 + t, is_connected=True, market_hours=True, now_monotonic=float(t), force_reconnect_fn=r)
    assert len(r.calls) == 1, "reconnect storm: a second reconnect fired before backoff elapsed"
    # after the real 10s backoff elapses, a second attempt is allowed.
    wd.check(tick_age=160.0, is_connected=True, market_hours=True, now_monotonic=11.0, force_reconnect_fn=r)
    assert len(r.calls) == 2


# --- Reconnect failure (critical failure after max attempts) ---

def test_reconnect_failure_reaches_critical_failure_and_stays_there():
    wd = _wd(max_consecutive_failures=2)
    r = _Recorder()
    wd.check(tick_age=150.0, is_connected=True, market_hours=True, now_monotonic=0.0, force_reconnect_fn=r)
    wd.check(tick_age=151.0, is_connected=True, market_hours=True, now_monotonic=1.0, force_reconnect_fn=r)   # attempt 1
    wd.check(tick_age=161.0, is_connected=True, market_hours=True, now_monotonic=11.0, force_reconnect_fn=r)  # attempt 2 (backoff=10s elapsed)
    state = wd.check(tick_age=171.0, is_connected=True, market_hours=True, now_monotonic=31.0, force_reconnect_fn=r)  # backoff=20s elapsed, attempt would be #3 > max=2
    assert state == WATCHDOG_CRITICAL_FAILURE
    assert len(r.calls) == 2, "a third reconnect should never have been attempted"
    # CRITICAL_FAILURE never silently self-heals, even with a real fresh tick.
    state2 = wd.check(tick_age=1.0, is_connected=True, market_hours=True, now_monotonic=100.0, force_reconnect_fn=r)
    assert state2 == WATCHDOG_CRITICAL_FAILURE
    assert len(r.calls) == 2


# --- Duplicate reconnect suppression (a real exception from the reconnect fn doesn't double-fire) ---

def test_reconnect_exception_does_not_crash_or_double_fire():
    wd = _wd()

    def failing_reconnect(reason):
        raise RuntimeError("real, simulated reconnect failure")

    wd.check(tick_age=150.0, is_connected=True, market_hours=True, now_monotonic=0.0, force_reconnect_fn=failing_reconnect)
    state = wd.check(tick_age=151.0, is_connected=True, market_hours=True, now_monotonic=1.0, force_reconnect_fn=failing_reconnect)
    assert state == WATCHDOG_RECONNECTING  # exception is caught, state still advances honestly
    assert wd.reconnect_attempt == 1


# --- Activation gating: never active outside market hours or while disconnected ---

def test_watchdog_inactive_outside_market_hours_even_when_silent():
    wd = _wd()
    r = _Recorder()
    state = wd.check(tick_age=99999.0, is_connected=True, market_hours=False, now_monotonic=0.0, force_reconnect_fn=r)
    assert state == WATCHDOG_HEALTHY
    assert r.calls == []


def test_watchdog_inactive_while_disconnected_even_when_silent():
    """A real, visible disconnect is out of this watchdog's scope (the
    feed's own is_connected/reconnect-count reporting already covers
    it) -- the watchdog exists specifically for 'connected but silent'."""
    wd = _wd()
    r = _Recorder()
    state = wd.check(tick_age=99999.0, is_connected=False, market_hours=True, now_monotonic=0.0, force_reconnect_fn=r)
    assert state == WATCHDOG_HEALTHY
    assert r.calls == []


def test_watchdog_resets_cleanly_when_market_hours_end_mid_silence():
    wd = _wd()
    r = _Recorder()
    wd.check(tick_age=150.0, is_connected=True, market_hours=True, now_monotonic=0.0, force_reconnect_fn=r)
    assert wd.watchdog_state == WATCHDOG_TICK_SILENCE
    state = wd.check(tick_age=99999.0, is_connected=True, market_hours=False, now_monotonic=1.0, force_reconnect_fn=r)
    assert state == WATCHDOG_HEALTHY
    assert wd.reconnect_attempt == 0


# --- Real, disclosed operational metrics are exposed and accurate ---

def test_operational_metrics_are_real_and_disclosed():
    wd = _wd()
    r = _Recorder()
    wd.check(tick_age=150.0, is_connected=True, market_hours=True, now_monotonic=100.0, force_reconnect_fn=r)
    assert wd.current_tick_age == 150.0
    assert wd.last_tick_timestamp == 100.0 - 150.0
    wd.check(tick_age=151.0, is_connected=True, market_hours=True, now_monotonic=101.0, force_reconnect_fn=r)
    assert wd.reconnect_attempt == 1
    assert "tick_silence" in wd.reconnect_reason


# --- FyersTickFeed.subscription_state (Subscription Validation deliverable) ---

def test_subscription_state_reflects_real_pending_symbols():
    feed = FyersTickFeed("app", "token", LOG, litemode=False)
    assert feed.subscription_state == "NONE"
    feed._pending_symbols.add("NSE:NIFTY50-INDEX")
    assert feed.subscription_state == "SUBSCRIBED"


def test_force_reconnect_preserves_pending_symbols():
    """Subscription restoration is performed deterministically by this
    class (documented conclusion, Sprint P1 Subscription Validation) --
    force_reconnect must never clear _pending_symbols, since that is
    what the real resubscribe-on-connect logic reads."""
    feed = FyersTickFeed("app", "token", LOG, litemode=False)
    feed._pending_symbols.add("NSE:NIFTY50-INDEX")
    feed._socket = None  # simulate no real socket yet, force_reconnect must not crash
    feed._started = True
    calls = []
    feed._connect = lambda: calls.append("connect")  # avoid a real SDK connection in this unit test
    feed.force_reconnect("test reason")
    assert feed._pending_symbols == {"NSE:NIFTY50-INDEX"}
    assert calls == ["connect"]


# --- health.py escalation: CRITICAL_FAILURE watchdog state -> overall_status RED ---

def test_health_snapshot_escalates_to_red_on_watchdog_critical_failure():
    from bujji.live_shadow_operator.health import build_health_snapshot, STATUS_RED
    from bujji.live_pipeline_bridge import SessionResult

    snap = build_health_snapshot(
        SessionResult(), reconnect_count=0, process_start_monotonic=0.0,
        watchdog_state="CRITICAL_FAILURE", watchdog_reconnect_attempt=3,
        watchdog_reconnect_reason="tick_silence_persists", subscription_state="SUBSCRIBED",
    )
    assert snap.overall_status == STATUS_RED
    assert any("CRITICAL_FAILURE" in r for r in snap.status_reasons)
    assert snap.watchdog_state == "CRITICAL_FAILURE"
    assert snap.watchdog_reconnect_attempt == 3
    assert snap.subscription_state == "SUBSCRIBED"


def test_health_snapshot_defaults_are_backward_compatible():
    """Every existing call site that never supplies watchdog kwargs must
    keep working unchanged -- purely additive, defaulted fields."""
    from bujji.live_shadow_operator.health import build_health_snapshot, STATUS_GREEN
    from bujji.live_pipeline_bridge import SessionResult

    snap = build_health_snapshot(SessionResult(), reconnect_count=0, process_start_monotonic=0.0)
    assert snap.watchdog_state is None
    assert snap.overall_status == STATUS_GREEN
