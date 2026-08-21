"""P3 Proof B — do FyersTickFeed's on_connect/on_message/on_close closures
carry any generation identity, or do they blindly act on whatever
self._socket currently is?

Fully deterministic, single-threaded (no timing luck required). We
monkeypatch only the third-party class reference (bujji.broker.fyers_ws
.data_ws.FyersDataSocket) with a controllable fake that records every
constructor call and every subscribe() call including which socket
instance received it. We never touch FyersTickFeed's own source.

Sequence:
  1. feed.start()          -> creates socket GEN-0, captures its real
                               on_connect closure (call it oc0).
  2. subscribe(["A"])      -> pending={"A"}, not connected yet.
  3. Invoke oc0()          -> simulates GEN-0's on_connect actually firing
                               (real SDK behavior). connected=True,
                               subscribe(["A"]) issued on GEN-0's socket.
  4. feed.force_reconnect("simulated watchdog trigger")
                            -> real FyersTickFeed code: closes GEN-0
                               (recorded), self._socket=None, then
                               _connect() creates GEN-1, captures its
                               on_connect closure oc1, launches connect
                               thread (faked, does not auto-fire oc1 in
                               this proof — step 5 does it explicitly to
                               control ordering).
  5. subscribe(["B"])      -> pending={"A","B"}, not connected (oc1 not
                               fired yet) -> no subscribe call issued yet.
  6. Invoke the SAVED oc0 AGAIN (simulating a late/delayed callback from
     the already-closed, abandoned GEN-0 socket -- exactly what a stale
     SDK-internal reconnect thread on the old object would eventually
     fire, per the real __on_close internal retry path).

Question: when the stale oc0 fires in step 6, which socket instance does
it act on?
"""
import sys
import types
import threading

# --- Build a fake data_ws module and inject it before importing fyers_ws ---
fake_pkg = types.ModuleType("fyers_apiv3")
fake_ws_pkg = types.ModuleType("fyers_apiv3.FyersWebsocket")
fake_data_ws = types.ModuleType("fyers_apiv3.FyersWebsocket.data_ws")

CALLS = []


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
        CALLS.append(("construct", self.gen))

    def connect(self):
        CALLS.append(("connect_thread_started", self.gen))
        # Deliberately does NOT auto-fire on_connect -- the proof script
        # controls firing explicitly to make the interleaving legible.

    def close_connection(self):
        self.closed = True
        CALLS.append(("close_connection", self.gen))

    def subscribe(self, symbols, data_type):
        CALLS.append(("subscribe", self.gen, tuple(symbols), self.closed))


fake_data_ws.FyersDataSocket = FakeSocket
fake_ws_pkg.data_ws = fake_data_ws
fake_pkg.FyersWebsocket = fake_ws_pkg
sys.modules["fyers_apiv3"] = fake_pkg
sys.modules["fyers_apiv3.FyersWebsocket"] = fake_ws_pkg
sys.modules["fyers_apiv3.FyersWebsocket.data_ws"] = fake_data_ws

sys.path.insert(0, "/opt/bujji/app")
import logging
from bujji.broker.fyers_ws import FyersTickFeed  # noqa: E402

log = logging.getLogger("p3proof")
log.addHandler(logging.NullHandler())

feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp/s120/p3")

# Step 1
feed.start()
socket_gen0 = feed._socket
assert socket_gen0.gen == 1

# Capture GEN-0's real on_connect closure the way the SDK itself would
# hold it: it was passed into FakeSocket.__init__ as on_connect.
oc0 = socket_gen0._on_connect

# Step 2
feed.subscribe(["A"])

# Step 3 — GEN-0's on_connect actually fires (real SDK behavior simulated).
oc0()
assert feed.is_connected is True

# Step 4 — watchdog-triggered force_reconnect.
feed.force_reconnect("simulated watchdog trigger")
socket_gen1 = feed._socket
assert socket_gen1.gen == 2
assert feed.is_connected is False  # force_reconnect sets connected=False and GEN-1's on_connect hasn't fired

oc1 = socket_gen1._on_connect

# Step 5
feed.subscribe(["B"])

calls_before_stale_fire = list(CALLS)

# Step 6 — the STALE, already-closed GEN-0 closure fires late.
oc0()

calls_after_stale_fire = CALLS[len(calls_before_stale_fire):]

print("=== Full call log ===")
for c in CALLS:
    print(" ", c)

print()
print("=== Calls triggered by the STALE gen-0 on_connect firing late ===")
for c in calls_after_stale_fire:
    print(" ", c)

# Which socket instance actually received the subscribe() call issued by
# the stale gen-0 closure?
stale_subscribe_calls = [c for c in calls_after_stale_fire if c[0] == "subscribe"]
assert stale_subscribe_calls, "stale closure did not subscribe -- unexpected, re-check pending symbols"
stale_call = stale_subscribe_calls[0]
target_gen = stale_call[1]

print()
print(f"feed._socket is now generation: {feed._socket.gen}")
print(f"The stale gen-0 closure's subscribe() call landed on generation: {target_gen}")
print(f"feed.is_connected after stale gen-0 fire: {feed.is_connected}")
print(f"feed._connect_count after stale gen-0 fire: {feed._connect_count}")

confirmed = (target_gen == feed._socket.gen == 2) and (target_gen != socket_gen0.gen)
print()
print(f"CONFIRMED — stale closure acts on the CURRENT (wrong-generation) socket: {confirmed}")
print(f"CONFIRMED — stale closure also silently flips feed._connected back to True: {feed.is_connected}")
