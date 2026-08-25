"""Synthetic FYERS websocket for the sandbox deadline proof.

NOT A MOCK OF CONVENIENCE. This shadows fyers_apiv3 on PYTHONPATH so the REAL
gate1_opening_capture.py runs, under the REAL systemd sandbox, with no network
socket and no credential. Everything under test -- the sustained loop, the
deadline, feed shutdown, sealing, accounting -- is the production code path.

It emits synthetic ticks shaped like the real ones so the corpus, the offline
re-parse and the accounting identity all have genuine input to work on.

Telemetry (connect/subscribe/close counts) is written to
GATE1_SANDBOXTEST_TELEMETRY so the test can assert feed shutdown was called
exactly once without reaching inside the process.
"""
import json
import os
import threading
import time

_TELEMETRY = os.environ.get("GATE1_SANDBOXTEST_TELEMETRY", "/tmp/stub_telemetry.json")
_TICK_HZ = float(os.environ.get("GATE1_SANDBOXTEST_TICK_HZ", "50"))

_state = {"connect_calls": 0, "subscribe_calls": 0, "close_calls": 0,
          "symbols_subscribed": 0, "ticks_emitted": 0}
_lock = threading.Lock()


def _flush():
    try:
        with open(_TELEMETRY, "w") as fh:
            json.dump(_state, fh, indent=1)
    except Exception:
        pass


class FyersDataSocket:
    def __init__(self, access_token=None, log_path=None, litemode=False,
                 write_to_file=False, reconnect=True,
                 on_connect=None, on_close=None, on_error=None, on_message=None,
                 **_kw):
        self._on_connect = on_connect
        self._on_close = on_close
        self._on_message = on_message
        self._symbols = []
        self._stop = threading.Event()
        self._emitter = None
        # A synthetic token must never look like a real one.
        assert "SANDBOXTEST" in str(access_token), (
            "the sandbox proof must run with a synthetic credential")

    def connect(self):
        with _lock:
            _state["connect_calls"] += 1
        _flush()
        if self._on_connect:
            self._on_connect()
        self._emitter = threading.Thread(target=self._emit, daemon=True)
        self._emitter.start()
        # The real connect() blocks; mirror that so the caller's daemon thread
        # behaves the same way.
        while not self._stop.is_set():
            time.sleep(0.2)

    def _emit(self):
        """Synthetic ticks in the real payload shape."""
        i = 0
        interval = 1.0 / max(_TICK_HZ, 1.0)
        while not self._stop.is_set():
            syms = list(self._symbols)
            if syms and self._on_message:
                sym = syms[i % len(syms)]
                self._on_message({
                    "ltp": 100.0 + (i % 50) * 0.05,
                    "vol_traded_today": 1000 + i,
                    "exch_feed_time": int(time.time()),
                    "last_traded_time": int(time.time()),
                    "bid_price": 99.9, "ask_price": 100.1,
                    "bid_size": 50, "ask_size": 75,
                    "last_traded_qty": 25,
                    "tot_buy_qty": 5000, "tot_sell_qty": 4800,
                    "avg_trade_price": 100.0, "low_price": 98.0,
                    "high_price": 102.0, "open_price": 99.0,
                    "prev_close_price": 99.5, "lower_ckt": 0, "upper_ckt": 0,
                    "ch": 0.5, "chp": 0.5,
                    "type": "sf", "symbol": sym,
                })
                with _lock:
                    _state["ticks_emitted"] += 1
                i += 1
            time.sleep(interval)

    def subscribe(self, symbols=None, data_type=None):
        with _lock:
            _state["subscribe_calls"] += 1
            for s in (symbols or []):
                if s not in self._symbols:
                    self._symbols.append(s)
            _state["symbols_subscribed"] = len(self._symbols)
        _flush()

    def unsubscribe(self, symbols=None, data_type=None):
        pass

    def close_connection(self):
        with _lock:
            _state["close_calls"] += 1
        self._stop.set()
        _flush()
        if self._on_close:
            self._on_close("synthetic close")
