"""Production Reliability Sprint P3 — Concurrency & Lifetime Proof Audit.

Deterministic, executable proofs for the race/lifetime concerns raised in
docs/PRODUCTION_FORENSIC_AUDIT.md, scoped strictly to the market-data
ingestion layer (FyersTickFeed / the installed fyers_apiv3 SDK / the
TickSilenceWatchdog). No production code is modified, fixed, or
refactored by this file — it only observes and proves/disproves.

Three independent proofs:

  test_close_connection_noops_when_ws_object_is_none
      Direct, real (non-mocked) construction of the installed SDK's
      FyersDataSocket, calling close_connection() before connect() ever
      runs. Proves the forensic audit's claim about the real installed
      third-party library, not a re-implementation of it.

  test_stale_socket_closure_acts_on_current_generation_socket
      Uses a controlled fake SDK object (the real third-party class is
      swapped only at the import boundary bujji.broker.fyers_ws.data_ws
      .FyersDataSocket — FyersTickFeed's own code is untouched and real)
      to prove, with zero timing dependence, that FyersTickFeed's
      on_connect closure carries no generation identity: a stale
      callback captured by an abandoned socket, if it fires late, acts
      on whatever self._socket currently is — including silently
      re-asserting is_connected=True and incrementing connect_count.

  test_concurrent_reconnect_and_subscribe_does_not_crash_in_stress
      Real multi-threaded stress test hammering force_reconnect() and
      subscribe() concurrently (the two call sites that read the
      unguarded self._socket field) to check whether the theoretical
      unsynchronized-read race is empirically reachable as a hard crash
      (AttributeError/TypeError). Documents the real observed outcome —
      this is an empirical bound, not a proof of impossibility.
"""
from __future__ import annotations

import logging
import random
import threading
import time

import pytest
import fyers_apiv3.FyersWebsocket.data_ws as real_data_ws

import bujji.broker.fyers_ws as fyers_ws_module
from bujji.broker.fyers_ws import FyersTickFeed


def _null_logger() -> logging.Logger:
    log = logging.getLogger("p3_proof_test")
    log.addHandler(logging.NullHandler())
    return log


@pytest.fixture
def fake_data_ws_module(monkeypatch):
    """Swaps ONLY the third-party class reference that FyersTickFeed
    calls (bujji.broker.fyers_ws.data_ws.FyersDataSocket) for a
    controllable fake. FyersTickFeed's own source is never touched."""
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


# --------------------------------------------------------------------- #
# Proof A
# --------------------------------------------------------------------- #
def test_close_connection_noops_when_ws_object_is_none(tmp_path):
    sock = real_data_ws.FyersDataSocket(
        access_token="dummy:dummy",
        log_path=str(tmp_path),
        litemode=True,
        write_to_file=False,
        reconnect=True,
        on_message=lambda msg: None,
        on_error=lambda msg: None,
        on_connect=lambda: None,
        on_close=lambda msg: None,
    )

    assert sock._FyersDataSocket__ws_object is None
    assert sock.restart_flag is True

    result = sock.close_connection()

    # If close_connection() had actually torn anything down, restart_flag
    # would now be False (it is set unconditionally inside the guarded
    # body). It is unchanged -- the entire method body was skipped.
    assert result is None
    assert sock.restart_flag is True
    assert sock._FyersDataSocket__ws_object is None


# --------------------------------------------------------------------- #
# Proof B (uses the fixture-installed fake SDK — see conftest.py)
# --------------------------------------------------------------------- #
def test_stale_socket_closure_acts_on_current_generation_socket(fake_data_ws_module):
    FakeSocket, calls = fake_data_ws_module

    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")

    feed.start()
    socket_gen0 = feed._socket
    oc0 = socket_gen0._on_connect

    feed.subscribe(["A"])
    oc0()  # gen-0's on_connect actually fires
    assert feed.is_connected is True

    feed.force_reconnect("simulated watchdog trigger")
    socket_gen1 = feed._socket
    assert socket_gen1 is not socket_gen0
    assert feed.is_connected is False

    feed.subscribe(["B"])
    calls_before = len(calls)

    # The stale, already-closed gen-0 closure fires late (models a
    # zombie SDK-internal reconnect thread completing on the abandoned
    # object after our force_reconnect() has already moved on).
    oc0()

    new_calls = calls[calls_before:]
    subscribe_calls = [c for c in new_calls if c[0] == "subscribe"]
    assert subscribe_calls, "expected the stale closure to issue a subscribe() call"

    target_gen = subscribe_calls[0][1]
    assert target_gen == socket_gen1.gen  # landed on the CURRENT generation, not its own
    assert feed.is_connected is True  # stale closure silently re-asserted connected=True
    assert feed.connect_count == 2  # and incremented connect_count a second time


# --------------------------------------------------------------------- #
# Proof C
# --------------------------------------------------------------------- #
def test_concurrent_reconnect_and_subscribe_does_not_crash_in_stress(fake_data_ws_module):
    _, _ = fake_data_ws_module
    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")
    feed.start()
    time.sleep(0.01)

    errors = []
    errors_lock = threading.Lock()
    stop = threading.Event()

    def reconnector():
        while not stop.is_set():
            try:
                feed.force_reconnect("stress")
            except Exception as exc:  # noqa: BLE001
                with errors_lock:
                    errors.append(("force_reconnect", repr(exc)))

    def subscriber():
        i = 0
        while not stop.is_set():
            i += 1
            try:
                feed.subscribe([f"SYM{i % 7}"])
            except Exception as exc:  # noqa: BLE001
                with errors_lock:
                    errors.append(("subscribe", repr(exc)))

    threads = [threading.Thread(target=reconnector, daemon=True) for _ in range(4)]
    threads += [threading.Thread(target=subscriber, daemon=True) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(1.5)
    stop.set()
    for t in threads:
        t.join(timeout=2)

    # Empirical bound, not a proof of impossibility -- see
    # docs/CONCURRENCY_LIFETIME_PROOF_P3.md for the classification and
    # its limits.
    crash_errors = [e for e in errors if "AttributeError" in e[1] or "TypeError" in e[1]]
    assert not crash_errors, f"reproduced a hard crash under stress: {crash_errors}"
