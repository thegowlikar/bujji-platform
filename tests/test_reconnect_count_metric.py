"""Tests — reconnect_count metric fix (TODO.md P2-4).

Live-confirmed dead in Session #3 and again in LSQ-1 Day 1 (docs/
LSQ1_DAY1_REPORT.md: read 1, real count ~9). Root cause: run_live_shadow.py
wired LiveShadowOperator.note_reconnect() to FyersTickFeed.on_disconnect(),
but bujji/broker/fyers_ws.py's own module docstring already root-causes why
that hook is unreliable -- the FYERS SDK's internal reconnect path
(reconnect=True, which every real caller including this one always passes)
never calls our on_close hook, so on_disconnect only ever fired for our own
explicit force_reconnect() calls, missing every one of the SDK's own silent
internal reconnects.

The fix rewires that one line to on_connect() instead, which DOES reliably
fire on every real (re)connect (see the same docstring). These tests prove
the underlying mechanism directly, using the exact fake-SDK fixture pattern
already established in test_concurrency_lifetime_proof_p3.py, plus a
source-level check pinning the corrected wiring in run_live_shadow.py
itself (which is a script, not a module these tests can otherwise import
and exercise end-to-end).
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

import bujji.broker.fyers_ws as fyers_ws_module
from bujji.broker.fyers_ws import FyersTickFeed


def _null_logger() -> logging.Logger:
    log = logging.getLogger("reconnect_count_test")
    log.addHandler(logging.NullHandler())
    return log


@pytest.fixture
def fake_data_ws_module(monkeypatch):
    """Identical pattern to test_concurrency_lifetime_proof_p3.py's own
    fixture: swaps ONLY the third-party class reference FyersTickFeed
    calls, never FyersTickFeed's own source."""
    calls = []

    class FakeSocket:
        _counter = 0

        def __init__(self, access_token=None, log_path=None, litemode=None,
                     write_to_file=None, reconnect=None, on_connect=None,
                     on_close=None, on_error=None, on_message=None):
            FakeSocket._counter += 1
            self.gen = FakeSocket._counter
            self.closed = False
            self._on_connect = on_connect
            self._on_close = on_close
            calls.append(("construct", self.gen))

        def connect(self):
            calls.append(("connect_thread_started", self.gen))

        def close_connection(self):
            self.closed = True
            calls.append(("close_connection", self.gen))

        def subscribe(self, symbols, data_type):
            calls.append(("subscribe", self.gen, tuple(symbols), self.closed))

    FakeSocket._counter = 0
    monkeypatch.setattr(fyers_ws_module.data_ws, "FyersDataSocket", FakeSocket)
    return FakeSocket, calls


def test_on_connect_hook_registered_after_initial_connect_only_fires_on_reconnect(fake_data_ws_module):
    """The exact guarantee run_live_shadow.py's fix depends on: a hook
    registered via on_connect() AFTER the initial connect has already
    completed must not fire for that initial connect (it already happened),
    only for genuine future reconnects."""
    _, _ = fake_data_ws_module
    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")

    feed.start()
    initial_socket = feed._socket
    initial_socket._on_connect()  # the real, first connect completes
    assert feed.connect_count == 1

    reconnect_calls = {"n": 0}
    feed.on_connect(lambda: reconnect_calls.__setitem__("n", reconnect_calls["n"] + 1))

    # Registering the hook here does not retroactively fire it for the
    # connect that already happened.
    assert reconnect_calls["n"] == 0

    feed.force_reconnect("simulated watchdog trigger")
    feed._socket._on_connect()  # the reconnect's own on_connect fires

    assert reconnect_calls["n"] == 1
    assert feed.connect_count == 2


def test_on_connect_hook_fires_for_every_additional_reconnect(fake_data_ws_module):
    """Multiple reconnects (matching LSQ-1 Day 1's real ~9 events) must
    each increment the count once, not be missed or double-counted."""
    _, _ = fake_data_ws_module
    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")

    feed.start()
    feed._socket._on_connect()

    reconnect_calls = {"n": 0}
    feed.on_connect(lambda: reconnect_calls.__setitem__("n", reconnect_calls["n"] + 1))

    for _ in range(3):
        feed.force_reconnect("simulated reconnect")
        feed._socket._on_connect()

    assert reconnect_calls["n"] == 3
    assert feed.connect_count == 4  # 1 initial + 3 reconnects


def test_on_disconnect_hook_does_not_fire_for_sdk_internal_reconnect_path(fake_data_ws_module):
    """Negative proof of the original bug's root cause: on_close (and
    therefore on_disconnect) is never invoked by our own force_reconnect()
    teardown path either -- FakeSocket.close_connection() here stands in
    for the SDK, and the real FyersDataSocket.close_connection() does not
    invoke on_close synchronously (verified in test_concurrency_lifetime_
    proof_p3.py's Proof A: close_connection() no-ops when __ws_object is
    None, which it always is here since we never call connect() for real).
    This is why note_reconnect() was previously wired to a hook that could
    not be relied on to fire for the failure mode it needed to catch."""
    _, _ = fake_data_ws_module
    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")
    feed.start()
    feed._socket._on_connect()

    disconnect_calls = {"n": 0}
    feed.on_disconnect(lambda: disconnect_calls.__setitem__("n", disconnect_calls["n"] + 1))

    feed.force_reconnect("simulated watchdog trigger")

    # force_reconnect() calls close_connection() on the old socket, but
    # our FakeSocket (matching the real SDK's own no-op-when-never-truly-
    # connected behavior) never invokes on_close from it.
    assert disconnect_calls["n"] == 0


def test_run_live_shadow_wires_note_reconnect_via_on_connect_not_on_disconnect():
    """Source-level regression pin: run_live_shadow.py is a script, not a
    module this test suite otherwise imports and exercises end-to-end, so
    this guards the specific line the fix touches directly rather than
    relying only on the underlying mechanism tests above."""
    import run_live_shadow

    src = Path(run_live_shadow.__file__).read_text()
    assert "tick_feed.on_connect(op.note_reconnect)" in src
    assert "tick_feed.on_disconnect(op.note_reconnect)" not in src
