"""Termination must be observed inside the loops, and must not be survivable.

TWO DEFECTS, both proven against the 2026-08-21 session.

(1) THE SLEEP THAT OUTLASTS THE TIMEOUT. The position-management loop ended
with a bare `time.sleep(interval_s)` -- 60s for a naked position, 300s for a
defined-risk one -- and checked `termination_requested()` only at the TOP of
the loop. A SIGTERM arriving one second into a 300-second sleep was not
observed for another 299.

The unit sets `TimeoutStopSec=60` with `KillSignal=SIGTERM`, so systemd
escalates to SIGKILL at 60 seconds. SIGKILL cannot be caught: `finally` never
runs, `_eod_close()` never runs, and an open position is abandoned. The
orderly-stop path that `install_termination_handlers` exists to provide was
unreachable inside the window systemd allows -- during the interval where the
loop spends essentially all of its time.

(2) THE FEED THAT WOULD NOT STOP. From the installed fyers_apiv3 source:

    self.ws_thread = Thread(target=ws.run_forever)
    self.ws_thread.daemon = self.background_flag        # default False

    def close_connection(self):
        if self.__ws_object:            # <-- and reconnect sets this None
            self.restart_flag = False   #     BEFORE calling connect() again
            ...

So the SDK explicitly marks its websocket thread NON-daemon (overriding the
daemon status it would inherit from the thread we start it on), and
`close_connection()` no-ops entirely when called mid-reconnect -- never
clearing `restart_flag`.

Observed: the session logged SHUTDOWN at 15:33:45, logged "Tick feed stopped",
then printed "Attempting reconnect 1 of 5..." and stayed alive until systemd
killed it at 15:54:56. Twenty-one minutes after it believed it had finished.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _runner_module():
    spec = importlib.util.spec_from_file_location(
        "_runner_term_guard", REPO_ROOT / "bujji_options_os_runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheSleepIsInterruptible:
    def setup_method(self):
        self.mod = _runner_module()
        self.mod._TERMINATION_REQUESTED.clear()

    def teardown_method(self):
        self.mod._TERMINATION_REQUESTED.clear()

    def test_a_quiet_interval_sleeps_the_whole_time(self):
        napped = []
        out = self.mod.sleep_unless_terminated(
            300, slice_seconds=1.0, sleep_fn=napped.append)
        assert out is False
        assert sum(napped) == pytest.approx(300.0)

    def test_a_stop_requested_mid_sleep_is_observed_within_one_slice(self):
        """THE regression: this used to be observed 299 seconds later, long
        after systemd had SIGKILLed the process."""
        napped = []

        def _nap(seconds):
            napped.append(seconds)
            if len(napped) == 3:                     # SIGTERM at t=3s
                self.mod._TERMINATION_REQUESTED.set()

        out = self.mod.sleep_unless_terminated(
            300, slice_seconds=1.0, sleep_fn=_nap)
        assert out is True
        assert sum(napped) <= 4.0, (
            f"slept {sum(napped)}s after the stop request; TimeoutStopSec=60 "
            f"means anything beyond ~60s is a SIGKILL and an abandoned position")

    def test_a_stop_already_pending_sleeps_not_at_all(self):
        self.mod._TERMINATION_REQUESTED.set()
        napped = []
        assert self.mod.sleep_unless_terminated(300, sleep_fn=napped.append) is True
        assert napped == []

    def test_the_slice_bounds_the_delay_regardless_of_cadence(self):
        """The observation delay must depend on the slice, not on the
        configured interval -- 300s cadences are the dangerous ones."""
        for interval in (60, 300, 900):
            self.mod._TERMINATION_REQUESTED.clear()
            napped = []

            def _nap(seconds):
                napped.append(seconds)
                self.mod._TERMINATION_REQUESTED.set()

            self.mod.sleep_unless_terminated(interval, slice_seconds=1.0, sleep_fn=_nap)
            assert sum(napped) <= 1.0


class TestTheManagementLoopUsesIt:
    def test_the_bare_sleep_is_gone(self):
        """A structural check, because the behavioural one would need a real
        300-second loop. Paired with the tests above, which prove the helper."""
        import ast

        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_position_management")
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)]
        bare_sleeps = [c for c in calls
                       if getattr(c.func, "attr", None) == "sleep"]
        assert not bare_sleeps, (
            "_position_management still calls sleep() directly; a SIGTERM "
            "arriving during it is not observed until the interval elapses")
        assert any(getattr(c.func, "id", None) == "sleep_unless_terminated"
                   for c in calls)


class TestTheTickFeedCanActuallyBeStopped:
    class _FakeSocket:
        """Mirrors the real SDK's shape, including the two behaviours that
        made the live feed unstoppable."""

        def __init__(self):
            self.background_flag = False       # SDK default: NON-daemon thread
            self.restart_flag = True           # reconnect intent
            self.max_reconnect_attempts = 5
            self._ws_object = None             # None => close_connection no-ops
            self.close_called = False

        def connect(self):
            pass

        def close_connection(self):
            self.close_called = True
            if self._ws_object:                # the real guard
                self.restart_flag = False

    def _feed_with(self, socket, monkeypatch):
        import bujji.broker.fyers_ws as ws

        monkeypatch.setattr(ws.data_ws, "FyersDataSocket", lambda **kw: socket)
        monkeypatch.setattr(
            ws.threading, "Thread",
            lambda *a, **k: type("T", (), {"start": lambda self: None})())
        feed = ws.FyersTickFeed("APP-1", "TOKEN", logging.getLogger("t"),
                                log_path="/tmp")
        feed.start()
        return feed

    def test_the_sdk_thread_is_marked_daemon_before_connect(self, monkeypatch):
        socket = self._FakeSocket()
        self._feed_with(socket, monkeypatch)
        assert socket.background_flag is True, (
            "the SDK would create its websocket thread as non-daemon, and a "
            "non-daemon run_forever blocks interpreter exit indefinitely")

    def test_stop_clears_the_reconnect_intent_even_mid_reconnect(self, monkeypatch):
        """`_ws_object` is None -- exactly the state the SDK is in while
        backing off between reconnect attempts, where close_connection() does
        nothing at all."""
        socket = self._FakeSocket()
        feed = self._feed_with(socket, monkeypatch)

        feed.stop()

        assert socket.close_called is True
        assert socket.restart_flag is False, (
            "the reconnect loop survived stop() -- this is the 21-minute hang "
            "of 2026-08-21, where the feed kept reconnecting after SHUTDOWN")
        assert socket.max_reconnect_attempts == 0

    def test_stop_is_still_idempotent(self, monkeypatch):
        socket = self._FakeSocket()
        feed = self._feed_with(socket, monkeypatch)
        feed.stop()
        feed.stop()
        assert socket.restart_flag is False
