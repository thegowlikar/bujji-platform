"""A FyersDataSocket that behaves like the real one on a reconnect.

THE ONE BEHAVIOUR THAT MATTERS: when the transport comes back, the server has
forgotten the subscriptions. This fake reproduces exactly that -- it clears
its subscribed set on reconnect and emits nothing until somebody subscribes
again. A fake that kept the subscriptions would make the test pass against
the very defect it exists to catch.
"""
import os
import threading
import time


class FyersDataSocket:
    def __init__(self, access_token=None, log_path=None, litemode=False,
                 write_to_file=False, reconnect=True, on_connect=None,
                 on_close=None, on_error=None, on_message=None, **kw):
        self.on_connect = on_connect
        self.on_close = on_close
        self.on_error = on_error
        self.on_message = on_message
        self.subscribed = set()
        self.subscribe_calls = []          # the evidence the test reads
        self.emitted_after_reconnect = 0
        self._stop = threading.Event()
        self._reconnected = False
        self._t0 = None
        self._lock = threading.Lock()
        # Configured by environment because the code under test constructs
        # this class itself and cannot be asked to pass test parameters.
        #
        #   MODE=reconnect  the connection drops and comes back, and the
        #                   server has forgotten the subscriptions
        #   MODE=silent     the feed simply STOPS. No on_close, no on_connect,
        #                   socket still "up" -- a half-open connection, which
        #                   no reconnect handler can ever notice
        self.mode = os.environ.get("FAKE_MODE", "reconnect")
        self.drop_after_s = float(os.environ.get("FAKE_DROP_AFTER_S", "6"))
        self.reconnect_gap_s = float(os.environ.get("FAKE_RECONNECT_GAP_S", "1"))

    def connect(self):
        self._t0 = time.time()
        if self.on_connect:
            self.on_connect()
        threading.Thread(target=self._pump, daemon=True).start()
        while not self._stop.is_set():
            time.sleep(0.1)

    def _pump(self):
        dropped = False
        while not self._stop.is_set():
            now = time.time()
            if not dropped and now - self._t0 >= self.drop_after_s:
                dropped = True
                with self._lock:
                    self.subscribed.clear()
                if self.mode == "silent":
                    # NOTHING IS SIGNALLED. This is the case a reconnect
                    # handler cannot catch, because no reconnect occurs.
                    self.went_silent_at = now
                    continue
                # THE DROP. Subscriptions die with the connection.
                if self.on_close:
                    self.on_close("connection lost (simulated)")
                time.sleep(self.reconnect_gap_s)
                self._reconnected = True
                if self.on_connect:
                    self.on_connect()          # transport back, session empty
            with self._lock:
                syms = list(self.subscribed)
            for s in syms:
                if self.on_message:
                    self.on_message({"symbol": s, "ltp": 100.0, "type": "sf",
                                     "bid_price": 99.5, "ask_price": 100.5,
                                     "bid_size": 10, "ask_size": 10})
                    if self._reconnected:
                        self.emitted_after_reconnect += 1
            time.sleep(0.2)

    def subscribe(self, symbols=None, data_type=None):
        symbols = list(symbols or [])
        with self._lock:
            self.subscribed.update(symbols)
        self.subscribe_calls.append({
            "t": time.time(), "n": len(symbols),
            "after_reconnect": self._reconnected, "symbols": symbols})

    def unsubscribe(self, symbols=None, data_type=None):
        with self._lock:
            self.subscribed.difference_update(symbols or [])

    def close_connection(self):
        self._stop.set()

    def keep_running(self):
        while not self._stop.is_set():
            time.sleep(0.1)
