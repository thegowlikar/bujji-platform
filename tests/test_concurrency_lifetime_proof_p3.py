"""Production Reliability Sprint P3 — Concurrency & Lifetime Proof Audit.

Deterministic, executable proofs for the race/lifetime concerns raised in
docs/PRODUCTION_FORENSIC_AUDIT.md, scoped strictly to the market-data
ingestion layer (FyersTickFeed / the installed fyers_apiv3 SDK / the
TickSilenceWatchdog).

Proof A/C are unchanged from the original audit pass. Proof B's assertions
were flipped (see its own docstring) once the P1-2 fix landed -- it
previously encoded the confirmed bug as expected behavior; it now proves
the fix. Proofs D onward are new, added for the P1-2 fix itself
(generation-tagged, lifecycle-lock-serialized connection identity):

  test_close_connection_noops_when_ws_object_is_none  [A, unchanged]
      Direct, real (non-mocked) construction of the installed SDK's
      FyersDataSocket, proving the third-party no-op this whole fix exists
      to make harmless.

  test_stale_on_connect_closure_is_a_complete_noop_after_supersession  [B, revised]
      A stale on_connect closure (from an abandoned generation) now issues
      zero calls of any kind and mutates no state -- previously it acted on
      the CURRENT generation's socket, silently.

  test_concurrent_reconnect_and_subscribe_does_not_crash_in_stress  [C, unchanged]
      Real multi-threaded stress test; still an empirical no-hard-crash
      bound, not a proof of impossibility -- unaffected by the P1-2 fix.

  test_concurrent_force_reconnect_race_preserves_atomic_invariant  [D, new]
      Many real concurrent force_reconnect() calls; proves self._socket
      always matches self._generation after every completed reconnect.

  test_stale_on_message_does_not_refresh_current_tick_state  [E, new]
  test_stale_on_error_is_a_complete_noop_including_logging   [F, new]
      Stale on_message/on_error closures, fired after supersession, must
      not write payload data -- and, for on_error specifically, must not
      emit a log line either.

  test_callback_stale_after_check_does_not_subscribe_replacement_socket  [G, new]
      The TOCTOU case: a closure that PASSES its currency check and only
      becomes stale afterward (a concurrent reconnect completes while it's
      blocked mid-flight inside subscribe()) must still only ever act on
      its own captured socket reference, never the replacement. Proven
      using the fake fixture's own event-gated subscribe() -- no
      production test-only hook.

  test_normal_connect_reconnect_sequence_unaffected  [H, new]
      Single-threaded happy path: connect counts, resubscribe counts, and
      tick recording are bit-for-bit identical to pre-fix behavior.

  test_stop_synchronously_sets_is_connected_false  [I, new]
  test_force_reconnect_after_stop_does_not_construct_a_new_socket  [J, new]
  test_start_stop_race_leaves_no_dangling_socket  [K, new]
      stop()'s own lifecycle semantics: synchronous, not dependent on any
      callback firing; a late force_reconnect() after stop() is a no-op
      that constructs nothing; start()-vs-stop() races always converge to
      the same safe end state regardless of interleaving.

  test_connection_handle_populated_before_any_callback_can_observe_it  [L, new]
      Direct evidence for the ordering claim underpinning the whole
      handle mechanism: by the time start() returns, the handle's socket
      reference is already populated -- firing its on_connect immediately
      succeeds rather than failing on an unpopulated reference.
"""
from __future__ import annotations

import logging
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
    controllable fake. FyersTickFeed's own source is never touched.

    P1-2 fix additions: captures all four callbacks (not just on_connect,
    as the original fixture did), tags each instance with its construction
    order (`.gen`), and supports an optional, test-attached `subscribe_gate`
    (a threading.Event) so a test can block a specific instance's
    subscribe() call mid-flight -- entirely inside this fixture, never in
    production code.
    """
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
            self._on_error = on_error
            self._on_message = on_message
            self.subscribe_gate = None  # test-attached threading.Event, optional
            self.subscribe_entered = None  # test-attached threading.Event, optional -- set the instant subscribe() is entered, before it waits on subscribe_gate
            self.close_synchronously_calls_on_close = False  # test-attached, optional
            calls.append(("construct", self.gen))

        def connect(self):
            calls.append(("connect_thread_started", self.gen))

        def close_connection(self):
            self.closed = True
            calls.append(("close_connection", self.gen))
            if self.close_synchronously_calls_on_close and self._on_close is not None:
                self._on_close("simulated synchronous SDK close")

        def subscribe(self, symbols, data_type):
            if self.subscribe_entered is not None:
                self.subscribe_entered.set()
            if self.subscribe_gate is not None:
                self.subscribe_gate.wait()
            calls.append(("subscribe", self.gen, tuple(symbols), self.closed))

    FakeSocket._counter = 0
    monkeypatch.setattr(fyers_ws_module.data_ws, "FyersDataSocket", FakeSocket)
    return FakeSocket, calls


# --------------------------------------------------------------------- #
# Proof A (unchanged)
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

    assert result is None
    assert sock.restart_flag is True
    assert sock._FyersDataSocket__ws_object is None


# --------------------------------------------------------------------- #
# Proof B (revised for the P1-2 fix -- assertions flipped, see module
# docstring: this test previously encoded the confirmed bug as expected.)
# --------------------------------------------------------------------- #
def test_stale_on_connect_closure_is_a_complete_noop_after_supersession(fake_data_ws_module):
    FakeSocket, calls = fake_data_ws_module

    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")

    feed.start()
    socket_gen0 = feed._socket
    oc0 = socket_gen0._on_connect

    feed.subscribe(["A"])
    oc0()  # gen-0's on_connect actually fires
    assert feed.is_connected is True
    assert feed.connect_count == 1

    feed.force_reconnect("simulated watchdog trigger")
    socket_gen1 = feed._socket
    assert socket_gen1 is not socket_gen0
    assert feed.is_connected is False

    feed.subscribe(["B"])
    calls_before = len(calls)
    connect_count_before = feed.connect_count

    # The stale, already-superseded gen-0 closure fires late (models a
    # zombie SDK-internal reconnect thread completing on the abandoned
    # object after our force_reconnect() has already moved on).
    oc0()

    # P1-2 fix: a complete no-op -- zero calls of any kind, zero state change.
    assert len(calls) == calls_before, "a stale on_connect must issue zero calls of any kind"
    assert feed.is_connected is False
    assert feed.connect_count == connect_count_before


# --------------------------------------------------------------------- #
# Proof C (unchanged)
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

    crash_errors = [e for e in errors if "AttributeError" in e[1] or "TypeError" in e[1]]
    assert not crash_errors, f"reproduced a hard crash under stress: {crash_errors}"


# --------------------------------------------------------------------- #
# Proof D (new, P1-2)
# --------------------------------------------------------------------- #
def test_concurrent_force_reconnect_race_preserves_atomic_invariant(fake_data_ws_module):
    FakeSocket, calls = fake_data_ws_module
    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")
    feed.start()

    stop_event = threading.Event()
    completed = [0]
    count_lock = threading.Lock()

    def reconnector():
        while not stop_event.is_set():
            feed.force_reconnect("race stress")
            with count_lock:
                completed[0] += 1

    threads = [threading.Thread(target=reconnector, daemon=True) for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(1.0)
    stop_event.set()
    for t in threads:
        t.join(timeout=2)

    # The atomic invariant (P1-2 design §2.2): after every completed
    # reconnect operation, self._socket is exactly the socket created for
    # self._generation -- never an older one, never a torn assignment.
    assert feed._socket is not None
    assert feed._socket.gen == feed._generation
    # generation accounts for exactly: 1 initial connect + every completed
    # reconnect -- the lock serializes, it never drops a reconnect.
    assert feed._generation == 1 + completed[0]


# --------------------------------------------------------------------- #
# Proof E (new, P1-2)
# --------------------------------------------------------------------- #
def test_stale_on_message_does_not_refresh_current_tick_state(fake_data_ws_module):
    FakeSocket, calls = fake_data_ws_module
    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")
    feed.start()
    socket_gen0 = feed._socket
    om0 = socket_gen0._on_message
    socket_gen0._on_connect()

    feed.force_reconnect("advance generation")
    socket_gen1 = feed._socket
    socket_gen1._on_connect()

    om1 = socket_gen1._on_message
    om1({"symbol": "NIFTY", "ltp": 100.0, "type": "sf"})
    assert feed.latest("NIFTY") == 100.0

    # Stale gen-0's on_message fires late with a different value.
    om0({"symbol": "NIFTY", "ltp": 999.0, "type": "sf"})

    assert feed.latest("NIFTY") == 100.0, "a stale tick must be dropped, not recorded"


# --------------------------------------------------------------------- #
# Proof F (new, P1-2)
# --------------------------------------------------------------------- #
def test_stale_on_error_is_a_complete_noop_including_logging(fake_data_ws_module, caplog):
    FakeSocket, calls = fake_data_ws_module
    logger_name = "p1_2_stale_error_test"
    log = logging.getLogger(logger_name)
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")
    feed.start()
    socket_gen0 = feed._socket
    oe0 = socket_gen0._on_error
    socket_gen0._on_connect()

    feed.force_reconnect("advance generation")

    before_error = feed.last_error
    with caplog.at_level(logging.WARNING, logger=logger_name):
        caplog.clear()
        oe0("stale simulated error")

        assert feed.last_error == before_error, "a stale on_error must not alter last_error"
        error_records = [r for r in caplog.records if r.message == "tick_feed_error"]
        assert not error_records, "a stale on_error must not emit a log line at all"


# --------------------------------------------------------------------- #
# Proof G (new, P1-2) -- the TOCTOU case: stale AFTER the currency check
# passes, not stale from the start. Uses only the fake fixture's own
# event-gated subscribe(); no production test-only hook.
# --------------------------------------------------------------------- #
def test_callback_stale_after_check_does_not_subscribe_replacement_socket(fake_data_ws_module):
    FakeSocket, calls = fake_data_ws_module
    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")

    feed.start()
    socket_gen0 = feed._socket
    oc0 = socket_gen0._on_connect
    feed.subscribe(["A"])  # _pending_symbols persists across generations

    gate = threading.Event()
    entered = threading.Event()
    socket_gen0.subscribe_gate = gate
    socket_gen0.subscribe_entered = entered
    done = threading.Event()

    def run_stale_on_connect():
        oc0()  # passes its currency check (gen-0 still current at that instant),
                # then blocks mid-flight inside socket_gen0.subscribe()
        done.set()

    t = threading.Thread(target=run_stale_on_connect, daemon=True)
    t.start()
    assert entered.wait(timeout=2), "gen-0 closure never reached the blocked subscribe() call"

    # entered.set() fires the instant subscribe() is entered, before it
    # waits on subscribe_gate -- so this point is deterministic: T is
    # necessarily blocked inside socket_gen0.subscribe() right now, not
    # merely "probably scheduled by now" (the previous time.sleep(0.05)
    # version was a race against the thread scheduler, not a proof).
    feed.force_reconnect("race while gen-0 blocked in subscribe")
    socket_gen1 = feed._socket
    assert socket_gen1 is not socket_gen0

    # A real, legitimate, unblocked gen-1 connect+subscribe, for comparison.
    socket_gen1._on_connect()

    gate.set()  # release the blocked gen-0 call
    done.wait(timeout=2)
    t.join(timeout=2)

    subscribe_calls = [c for c in calls if c[0] == "subscribe"]
    gen0_subscribes = [c for c in subscribe_calls if c[1] == socket_gen0.gen]
    gen1_subscribes = [c for c in subscribe_calls if c[1] == socket_gen1.gen]

    assert len(gen0_subscribes) == 1, "the stale closure's subscribe must land on its own captured gen-0 socket"
    assert len(gen1_subscribes) == 1, "only the legitimate gen-1 connect's subscribe may target gen-1"


# --------------------------------------------------------------------- #
# Proof H (new, P1-2)
# --------------------------------------------------------------------- #
def test_normal_connect_reconnect_sequence_unaffected(fake_data_ws_module):
    FakeSocket, calls = fake_data_ws_module
    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")

    feed.start()
    feed.subscribe(["A", "B"])
    feed._socket._on_connect()
    assert feed.is_connected is True
    assert feed.connect_count == 1

    feed.force_reconnect("normal reconnect")
    feed._socket._on_connect()
    assert feed.is_connected is True
    assert feed.connect_count == 2

    feed._socket._on_message({"symbol": "X", "ltp": 42.0, "type": "sf"})
    assert feed.latest("X") == 42.0

    subscribe_calls = [c for c in calls if c[0] == "subscribe"]
    assert len(subscribe_calls) == 2  # one resubscribe per real connect, exactly as before the fix


# --------------------------------------------------------------------- #
# Proof I (new, P1-2)
# --------------------------------------------------------------------- #
def test_stop_synchronously_sets_is_connected_false(fake_data_ws_module):
    _, _ = fake_data_ws_module
    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")
    feed.start()
    feed._socket._on_connect()
    assert feed.is_connected is True

    feed.stop()

    # Set directly by stop() itself -- not dependent on any callback
    # (e.g. on_close) ever firing, which the P3 audit already established
    # cannot be relied upon for the SDK's own internal retry path.
    assert feed.is_connected is False


# --------------------------------------------------------------------- #
# Proof J (new, P1-2)
# --------------------------------------------------------------------- #
def test_force_reconnect_after_stop_does_not_construct_a_new_socket(fake_data_ws_module):
    FakeSocket, calls = fake_data_ws_module
    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")
    feed.start()
    counter_before_stop = FakeSocket._counter

    feed.stop()
    feed.force_reconnect("late watchdog call after shutdown")

    assert FakeSocket._counter == counter_before_stop, "no new socket may be constructed after stop()"
    assert feed._socket is None


# --------------------------------------------------------------------- #
# Proof K (new, P1-2)
# --------------------------------------------------------------------- #
def test_start_stop_race_leaves_no_dangling_socket(fake_data_ws_module):
    FakeSocket, calls = fake_data_ws_module
    log = _null_logger()

    for _ in range(100):  # many trials across real thread-scheduling interleavings
        FakeSocket._counter = 0
        calls.clear()
        feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")

        barrier = threading.Barrier(2)

        def do_start():
            barrier.wait()
            feed.start()

        def do_stop():
            barrier.wait()
            feed.stop()

        t1 = threading.Thread(target=do_start)
        t2 = threading.Thread(target=do_stop)
        t1.start()
        t2.start()
        t1.join(timeout=2)
        t2.join(timeout=2)

        assert feed._socket is None
        assert feed._stopped is True
        assert feed.is_connected is False
        assert FakeSocket._counter <= 1, "at most one real socket may ever be constructed"


# --------------------------------------------------------------------- #
# Proof L (new, P1-2)
# --------------------------------------------------------------------- #
def test_connection_handle_populated_before_any_callback_can_observe_it(fake_data_ws_module):
    FakeSocket, calls = fake_data_ws_module
    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")

    feed.start()  # by the time start() returns, construct -> populate handle ->
                  # assign -> start thread has already completed, synchronously,
                  # under the same lock hold.
    feed.subscribe(["Z"])
    feed._socket._on_connect()  # firing immediately must succeed, not raise on an
                                  # unpopulated handle.socket reference

    subscribe_calls = [c for c in calls if c[0] == "subscribe"]
    assert subscribe_calls, "expected a real subscribe call, proving the handle's socket field was already populated"
    assert subscribe_calls[0][1] == feed._socket.gen


# --------------------------------------------------------------------- #
# Proof M (new, P1-2 deadlock fix)
# --------------------------------------------------------------------- #
def test_force_reconnect_survives_synchronous_on_close_without_deadlock(fake_data_ws_module):
    """force_reconnect() must not hold _lifecycle_lock across the SDK's
    close_connection() call -- a synchronous on_close invoked from inside
    that call would otherwise try to re-enter the same non-reentrant lock
    from the same thread and block forever. This test's FakeSocket
    reproduces exactly that: close_connection() synchronously invokes its
    own saved on_close before returning, matching what a real close could
    legally do. If force_reconnect() ever regresses to holding
    _lifecycle_lock across the close call, this test hangs (bounded by
    join(timeout=...) below rather than hanging the whole suite) instead
    of completing."""
    FakeSocket, calls = fake_data_ws_module
    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")

    feed.start()
    socket_gen0 = feed._socket
    socket_gen0.close_synchronously_calls_on_close = True

    result = {}

    def run():
        feed.force_reconnect("proof: synchronous on_close must not deadlock")
        result["returned"] = True

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=2)

    assert result.get("returned") is True, "force_reconnect() deadlocked on a synchronous on_close"
    assert not t.is_alive()

    # The new generation must be fully usable: a real connect on it must
    # succeed and be recorded as this (new) generation's own connect, not
    # silently dropped as stale.
    socket_gen1 = feed._socket
    assert socket_gen1 is not socket_gen0
    socket_gen1._on_connect()
    assert feed.is_connected is True
    assert feed.connect_count == 1  # start() itself never fires on_connect in this fixture (matching every
                                     # other test in this file) -- this is the new generation's own real connect


# --------------------------------------------------------------------- #
# Proof M (new, Phase 17F.6.2) -- empirically traces whether
# force_reconnect()'s close_connection() call fires the OLD generation's
# on_close closure, and whether that firing reaches the public
# on_disconnect() hooks a future capture-lifecycle adapter would
# register.
#
# Source-level trace (installed fyers_apiv3 SDK, confirmed by reading
# data_ws.py + the underlying `websocket` package's _app.py) established
# that a manual close_connection() call DOES synchronously invoke the
# registered on_close callback before returning (restart_flag is set to
# False before .close() is called, so __on_close takes the "real close"
# branch rather than the SDK's own silent internal-retry branch; the
# underlying websocket-client library's own teardown() calls the on_close
# callback before run_forever() returns, and close_connection() blocks on
# ws_thread.join() until that has happened). This test proves the
# CONSEQUENCE of that fact for FyersTickFeed specifically: whether the
# outer on_disconnect() hook seam actually sees anything.
# --------------------------------------------------------------------- #
def test_force_reconnect_close_fires_synchronously_but_is_suppressed_as_stale(fake_data_ws_module):
    """The empirical answer to the Phase 17F.6.2 open question: a
    watchdog-forced reconnect's close_connection() call DOES trigger the
    old generation's on_close closure synchronously (proven by actually
    firing it, via the fixture's close_synchronously_calls_on_close
    flag) -- but FyersTickFeed's own currency check has ALREADY nulled
    _current_handle before close_connection() is called, so the closure
    is a complete no-op: zero state mutation, zero log line, and
    critically, the public on_disconnect() hook is NEVER invoked.

    CONSEQUENCE FOR PHASE 17F.6.2's DESIGN: an adapter that only
    registers via feed.on_disconnect(hook) would observe NOTHING for a
    watchdog-forced reconnect -- only the subsequent on_connect (Proof N
    below) fires. Any future CaptureLifecycleTracker integration must
    treat force_reconnect() as its own, separate emission point (e.g. the
    adapter calling tracker.record_condition() itself, driven by
    TickSilenceWatchdog's own state transitions) rather than relying on
    the disconnect hook to fire for this path.
    """
    FakeSocket, calls = fake_data_ws_module

    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")

    disconnect_hook_calls = []
    feed.on_disconnect(lambda: disconnect_hook_calls.append("fired"))

    feed.start()
    gen0_socket = feed._socket
    # Arm the fake so close_connection() synchronously invokes the real
    # on_close closure -- mirroring the traced real SDK behaviour, not
    # skipping straight to "assume it doesn't fire."
    gen0_socket.close_synchronously_calls_on_close = True

    feed.force_reconnect("test-driven reconnect")

    # The old generation's socket really was closed, and its on_close
    # closure really did execute (not skipped) -- this is the "fires
    # synchronously" half of the empirical claim.
    assert gen0_socket.closed is True
    assert ("close_connection", gen0_socket.gen) in calls

    # And yet the public hook never saw it -- this is the "suppressed as
    # stale" half. A future adapter relying solely on on_disconnect()
    # would be silently blind to every watchdog-forced reconnect.
    assert disconnect_hook_calls == []

    # feed.is_connected was set to False directly by force_reconnect()
    # itself (not by the stale closure, which never touched it) and then
    # set back to True once the NEW generation actually connects -- so at
    # this point, before the new generation's on_connect has fired, it
    # correctly still reads False.
    assert feed.is_connected is False


def test_force_reconnect_new_generation_on_connect_still_fires_normally(fake_data_ws_module):
    """The other half of the empirical trace: the NEW generation built by
    force_reconnect() is unaffected by the old generation's suppressed
    close -- its own on_connect closure fires normally and IS observable
    via the public on_connect() hook, exactly like a first-time connect.
    This is the only hook a force_reconnect()-driven adapter would
    currently see."""
    FakeSocket, calls = fake_data_ws_module

    log = _null_logger()
    feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp")

    connect_hook_calls = []
    feed.on_connect(lambda: connect_hook_calls.append("fired"))

    feed.start()
    gen0_socket = feed._socket
    gen0_socket._on_connect()  # bring gen-0 up first, so the reconnect is a real transition
    assert feed.connect_count == 1
    assert connect_hook_calls == ["fired"]

    feed.force_reconnect("test-driven reconnect")
    gen1_socket = feed._socket
    assert gen1_socket is not gen0_socket

    gen1_socket._on_connect()  # the NEW generation's real connect callback

    assert feed.is_connected is True
    assert feed.connect_count == 2
    assert connect_hook_calls == ["fired", "fired"]
