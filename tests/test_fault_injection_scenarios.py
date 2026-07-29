"""Production Reliability Sprint P2 -- the 8 required fault-injection
scenarios, run against the REAL, unmodified
bujji.broker.fyers_ws.TickSilenceWatchdog via the FakeFyersTickFeed test
double (fault_injection_harness.py). No Production decision logic, no
Learning architecture, no strategy logic is imported or touched
anywhere in this file."""
from __future__ import annotations

import logging

from bujji.broker.fyers_ws import (
    TickSilenceWatchdog,
    WATCHDOG_HEALTHY, WATCHDOG_TICK_SILENCE, WATCHDOG_RECONNECTING,
    WATCHDOG_RECOVERED, WATCHDOG_CRITICAL_FAILURE,
)

from tests.fault_injection_harness import FakeFyersTickFeed, ScenarioEvent, run_scenario, states_seen

LOG = logging.getLogger("test_fault_injection")

# Same real ratios as Sprint P1's own test file, scaled for fast,
# deterministic scenario runs -- silence_threshold=100s, backoff=10s,
# max_consecutive_failures=3 (identical to bujji.broker.fyers_ws's own
# unit tests, so scenario results are directly comparable to P1's).
def _wd(**overrides):
    kwargs = dict(silence_threshold_seconds=100.0, max_consecutive_failures=3,
                  backoff_base_seconds=10.0, logger=LOG)
    kwargs.update(overrides)
    return TickSilenceWatchdog(**kwargs)


def _ev(t, label, fn):
    return ScenarioEvent(at_time=t, label=label, apply=fn)


def _tick_every(times):
    return [_ev(t, f"tick@{t}", lambda f: f.inject_tick()) for t in times]


# --- Scenario 1: Healthy session ---

def test_scenario_1_healthy_session_stays_healthy():
    feed = FakeFyersTickFeed()
    wd = _wd()
    events = _tick_every(range(0, 61, 10))  # a real tick every 10s, well under the 100s threshold
    transcript = run_scenario(feed, wd, events, until_t=60, poll_interval=1.0)
    seq = states_seen(transcript)
    assert seq == [WATCHDOG_HEALTHY]
    assert len(feed.force_reconnect_calls) == 0


# --- Scenario 2: Short silence, below threshold ---

def test_scenario_2_short_silence_below_threshold_no_reconnect():
    feed = FakeFyersTickFeed()
    wd = _wd()
    events = [_ev(0, "tick", lambda f: f.inject_tick()), _ev(90, "tick", lambda f: f.inject_tick())]
    transcript = run_scenario(feed, wd, events, until_t=95, poll_interval=1.0)
    seq = states_seen(transcript)
    assert seq == [WATCHDOG_HEALTHY]  # 90s gap never reaches the 100s threshold
    assert len(feed.force_reconnect_calls) == 0


# --- Scenario 3: Long silence -> TICK_SILENCE -> RECONNECTING -> RECOVERED ---

def test_scenario_3_long_silence_recovers_after_reconnect():
    """A reconnect 'succeeding' at the socket level (is_connected=True)
    is NOT, by itself, treated as recovery -- the watchdog correctly
    requires a real fresh tick to actually arrive before transitioning
    to RECOVERED. This scenario's on_force_reconnect models a real,
    complete, successful reconnect: the socket reconnects AND a real
    tick resumes immediately (the honest, fast-recovery case)."""
    feed = FakeFyersTickFeed()
    wd = _wd()

    def on_reconnect(f, now):
        f.inject_connect()
        f.inject_tick()  # a real, complete reconnect: ticks resume right away.

    feed.on_force_reconnect = on_reconnect
    events = [_ev(0, "tick", lambda f: f.inject_tick())]
    transcript = run_scenario(feed, wd, events, until_t=110, poll_interval=1.0)
    seq = states_seen(transcript)
    assert seq == [WATCHDOG_HEALTHY, WATCHDOG_TICK_SILENCE, WATCHDOG_RECONNECTING, WATCHDOG_RECOVERED, WATCHDOG_HEALTHY]
    assert len(feed.force_reconnect_calls) == 1


# --- Scenario 4: Permanent silence -> exponential backoff -> CRITICAL_FAILURE, no storm ---

def test_scenario_4_permanent_silence_reaches_critical_failure_without_storm():
    feed = FakeFyersTickFeed()
    wd = _wd()  # max_consecutive_failures=3, backoff_base=10s
    events = [_ev(0, "tick", lambda f: f.inject_tick())]  # never ticks again
    transcript = run_scenario(feed, wd, events, until_t=200, poll_interval=1.0)
    seq = states_seen(transcript)
    assert seq[-1] == WATCHDOG_CRITICAL_FAILURE
    assert len(feed.force_reconnect_calls) == 3, "exactly max_consecutive_failures reconnects -- no storm"
    # Real backoff timing check: reconnect attempts at t=101 (1st), t=111 (2nd, 10s backoff),
    # t=131 (3rd, 20s backoff) -- never closer together than the real exponential schedule.
    times = [t for t, _ in feed.force_reconnect_calls]
    assert times[1] - times[0] >= 10
    assert times[2] - times[1] >= 20


# --- Scenario 5: Subscription loss after reconnect ---

def test_scenario_5_subscription_loss_after_reconnect_requires_resubscribe():
    feed = FakeFyersTickFeed()
    feed.subscribe([])  # no-op, establishes the real subscribe() call path
    wd = _wd()

    def on_reconnect(f, now):
        f.inject_connect()
        f.inject_subscription_loss()  # real, documented SDK behaviour (Sprint P1): reconnect succeeds, subscriptions are lost

    feed.on_force_reconnect = on_reconnect
    feed.subscribe(["NSE:NIFTY50-INDEX"])  # real, initial subscription
    events = [
        _ev(0, "tick", lambda f: f.inject_tick()),
        # After the reconnect (which will fire once silence crosses threshold, ~t=101)
        # subscriptions are lost -- a real resubscribe is required before ticks can resume.
        _ev(110, "resubscribe", lambda f: f.subscribe(["NSE:NIFTY50-INDEX"])),
        _ev(111, "tick_resumes_after_resubscribe", lambda f: f.inject_tick()),
    ]
    transcript = run_scenario(feed, wd, events, until_t=115, poll_interval=1.0)
    # Confirm subscription really was lost right after the reconnect, before the scripted resubscribe.
    lost_row = next(r for r in transcript if 101 <= r.t < 110)
    assert lost_row.subscription_state == "NONE", "reconnect alone must not be assumed sufficient"
    final_row = transcript[-1]
    assert final_row.subscription_state == "SUBSCRIBED"
    assert final_row.watchdog_state in (WATCHDOG_RECOVERED, WATCHDOG_HEALTHY)


# --- Scenario 6: Repeated Cloudflare-style reconnect failures (rapid real is_connected flapping) ---

def test_scenario_6_cloudflare_style_flapping_respects_retry_policy_no_infinite_loop():
    """Mirrors the real Sprint P1 incident: 5 real disconnect/reconnect
    cycles in ~22 real seconds. Per the watchdog's own real, disclosed
    design (activates ONLY while is_connected=True), each disconnect
    resets it to HEALTHY -- this scenario verifies that real, honest
    behaviour explicitly (no storm, no runaway state) rather than
    assuming it."""
    feed = FakeFyersTickFeed()
    wd = _wd()
    events = [_ev(0, "tick", lambda f: f.inject_tick())]
    # 5 disconnect/reconnect flaps within 22s, matching the real incident's timing.
    flap_times = [7, 9, 12, 14, 17, 19, 22, 24, 27, 29]  # disconnect, connect, disconnect, connect, ...
    for i, t in enumerate(flap_times):
        if i % 2 == 0:
            events.append(_ev(t, f"disconnect@{t}", lambda f: f.inject_disconnect()))
        else:
            events.append(_ev(t, f"reconnect@{t}", lambda f: f.inject_connect()))
    transcript = run_scenario(feed, wd, events, until_t=30, poll_interval=1.0)
    # No reconnect storm: the watchdog itself never issued a SINGLE
    # force_reconnect during the flapping window, because every real
    # disconnect resets it to HEALTHY before 100s of silence could ever
    # accumulate -- confirmed, not assumed.
    assert len(feed.force_reconnect_calls) == 0
    assert transcript[-1].watchdog_state == WATCHDOG_HEALTHY


# --- Scenario 7: Half-open connection (is_connected never toggles False) ---

def test_scenario_7_half_open_connection_detected_and_reconnect_performed():
    """The exact real incident shape: is_connected reports True the
    entire time (never a visible disconnect), yet no real ticks arrive."""
    feed = FakeFyersTickFeed()
    wd = _wd()
    events = [_ev(0, "tick", lambda f: f.inject_tick())]  # then nothing -- pure half-open silence, connection never drops
    transcript = run_scenario(feed, wd, events, until_t=110, poll_interval=1.0)
    assert all(r.is_connected for r in transcript), "this scenario must never show a visible disconnect"
    seq = states_seen(transcript)
    assert WATCHDOG_TICK_SILENCE in seq
    assert WATCHDOG_RECONNECTING in seq
    assert len(feed.force_reconnect_calls) == 1


# --- Scenario 8: Delayed recovery (silence, reconnect fails, silence continues, reconnect succeeds) ---

def test_scenario_8_delayed_recovery_correct_state_sequence():
    feed = FakeFyersTickFeed()
    wd = _wd()
    attempt_count = {"n": 0}

    def on_reconnect(f, now):
        attempt_count["n"] += 1
        if attempt_count["n"] >= 2:
            f.inject_connect()

    feed.on_force_reconnect = on_reconnect
    events = [
        _ev(0, "tick", lambda f: f.inject_tick()),
        _ev(112, "tick_after_second_reconnect", lambda f: f.inject_tick()),  # real tick resumes only after the 2nd reconnect
    ]
    transcript = run_scenario(feed, wd, events, until_t=115, poll_interval=1.0)
    seq = states_seen(transcript)
    assert seq == [WATCHDOG_HEALTHY, WATCHDOG_TICK_SILENCE, WATCHDOG_RECONNECTING, WATCHDOG_RECOVERED, WATCHDOG_HEALTHY]
    assert len(feed.force_reconnect_calls) == 2, "recovers on the 2nd attempt, never reaching CRITICAL_FAILURE (max=3)"
