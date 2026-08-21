"""P3 Proof C — concurrent stress test of the unguarded self._socket
field. Real threads, real GIL-driven interleaving (not scripted), many
iterations. Question: is a hard crash (AttributeError/TypeError) actually
reachable, or does the None-check in the closures make it benign in
practice?
"""
import sys
import types
import threading
import time
import random

fake_pkg = types.ModuleType("fyers_apiv3")
fake_ws_pkg = types.ModuleType("fyers_apiv3.FyersWebsocket")
fake_data_ws = types.ModuleType("fyers_apiv3.FyersWebsocket.data_ws")

ERRORS = []
ERRORS_LOCK = threading.Lock()


class FakeSocket:
    def __init__(self, access_token=None, log_path=None, litemode=None,
                 write_to_file=None, reconnect=None, on_connect=None,
                 on_close=None, on_error=None, on_message=None):
        self.closed = False
        self._on_connect = on_connect

    def connect(self):
        # Simulate real async handshake latency, then fire on_connect
        # from a SEPARATE thread -- exactly like the real SDK does.
        def fire():
            time.sleep(random.uniform(0, 0.003))
            try:
                if not self.closed:
                    self._on_connect()
            except Exception as exc:  # noqa: BLE001
                with ERRORS_LOCK:
                    ERRORS.append(("on_connect_thread", repr(exc)))
        threading.Thread(target=fire, daemon=True).start()

    def close_connection(self):
        self.closed = True

    def subscribe(self, symbols, data_type):
        pass


fake_data_ws.FyersDataSocket = FakeSocket
fake_ws_pkg.data_ws = fake_data_ws
fake_pkg.FyersWebsocket = fake_ws_pkg
sys.modules["fyers_apiv3"] = fake_pkg
sys.modules["fyers_apiv3.FyersWebsocket"] = fake_ws_pkg
sys.modules["fyers_apiv3.FyersWebsocket.data_ws"] = fake_data_ws

sys.path.insert(0, "/opt/bujji/app")
import logging
from bujji.broker.fyers_ws import FyersTickFeed  # noqa: E402

log = logging.getLogger("p3proof_c")
log.addHandler(logging.NullHandler())

N_TRIALS = 5000
crashes = 0
trial_errors = []

feed = FyersTickFeed(app_id="X", access_token="Y", logger=log, log_path="/tmp/s120/p3")
feed.start()
time.sleep(0.01)

stop = threading.Event()


def reconnector():
    while not stop.is_set():
        try:
            feed.force_reconnect("stress")
        except Exception as exc:  # noqa: BLE001
            with ERRORS_LOCK:
                ERRORS.append(("force_reconnect_thread", repr(exc)))


def subscriber():
    i = 0
    while not stop.is_set():
        i += 1
        try:
            feed.subscribe([f"SYM{i % 7}"])
        except Exception as exc:  # noqa: BLE001
            with ERRORS_LOCK:
                ERRORS.append(("subscribe_thread", repr(exc)))


threads = [threading.Thread(target=reconnector, daemon=True) for _ in range(4)]
threads += [threading.Thread(target=subscriber, daemon=True) for _ in range(4)]
for t in threads:
    t.start()

time.sleep(3.0)
stop.set()
for t in threads:
    t.join(timeout=2)

print(f"Ran concurrent force_reconnect()/subscribe() stress for 3.0s with 8 threads.")
print(f"Errors observed in FyersTickFeed's own code (main-thread or hook calls): {len(ERRORS)}")
for kind, msg in ERRORS[:30]:
    print(f"  [{kind}] {msg}")
print()
print(f"is_connected at end: {feed.is_connected}")
print(f"connect_count at end: {feed.connect_count}")
print(f"CRASH REACHED (AttributeError/TypeError on self._socket): "
      f"{any('AttributeError' in m or 'TypeError' in m for _, m in ERRORS)}")
