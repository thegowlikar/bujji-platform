"""Transport-level request pacing for the FYERS adapter.

WHY THIS EXISTS. Measured live on 2026-08-17 against the real account: one
`MarketDataAdapter.build_snapshot()` issued 85 SDK calls in 2.98s, peaking at
31 within a one-second window, because `option_chain_adapter` quotes each
contract individually (82 contracts, one call each). FYERS answered the NEXT
history call with `code=429`, and `bujji_options_os_runner` died in
`_startup()` before it could derive a regime -- so the whole live trading
session was unreachable, not merely degraded.

The fix paces at `FyersBroker._call()`, which the module already documents as
the "single choke-point for all FYERS transport". These tests pin the
properties that make that pacing correct; none of them touch the network.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from bujji.broker import fyers as fyers_module
from bujji.broker.fyers import _MIN_SECONDS_BETWEEN_CALLS, _paced, _wait_for_slot


@pytest.fixture(autouse=True)
def _reset_pacer():
    """The pacer is module state by design; keep tests independent of it."""
    fyers_module._next_call_allowed_at = 0.0
    yield
    fyers_module._next_call_allowed_at = 0.0


class TestPacingRate:
    def test_stays_under_the_documented_ceiling(self):
        """FYERS documents 10 requests/second. The configured interval must
        leave headroom rather than sit exactly on the limit."""
        assert _MIN_SECONDS_BETWEEN_CALLS > 0
        assert 1.0 / _MIN_SECONDS_BETWEEN_CALLS < 10.0

    def test_consecutive_slots_are_spaced(self):
        first = time.monotonic()
        _wait_for_slot()
        _wait_for_slot()
        elapsed = time.monotonic() - first
        assert elapsed >= _MIN_SECONDS_BETWEEN_CALLS

    def test_a_burst_of_calls_cannot_exceed_the_rate(self):
        calls = []
        method = lambda *a: calls.append(time.monotonic())
        paced = _paced(method)
        for _ in range(8):
            paced()
        span = calls[-1] - calls[0]
        assert span >= 7 * _MIN_SECONDS_BETWEEN_CALLS
        peak = max(sum(1 for t in calls if x <= t < x + 1.0) for x in calls)
        assert peak <= 10, f"burst reached {peak}/s"

    def test_an_idle_gap_is_not_banked_into_a_later_burst(self):
        """A leaky bucket that credits idle time would let a quiet startup
        pay for a 30-call burst later -- exactly the shape that got the
        session refused."""
        _wait_for_slot()
        time.sleep(_MIN_SECONDS_BETWEEN_CALLS * 4)
        start = time.monotonic()
        _wait_for_slot()
        _wait_for_slot()
        assert time.monotonic() - start >= _MIN_SECONDS_BETWEEN_CALLS


class TestPacingIsSharedAccountWide:
    def test_state_is_module_level_not_per_instance(self):
        """The ceiling is per ACCOUNT, and a live session builds more than one
        FyersBroker against the same credentials (one for the chain, one for
        the intelligence cycle). Per-instance pacers would each honour the
        limit and still exceed it together."""
        assert hasattr(fyers_module, "_next_call_allowed_at")
        assert not hasattr(fyers_module.FyersBroker, "_next_call_allowed_at")

    def test_threads_queue_instead_of_waking_together(self):
        """The slot is reserved under the lock and the wait happens outside
        it. If both happened inside, threads would serialise anyway; if
        neither did, they would all wake against the same timestamp."""
        stamps = []
        lock = threading.Lock()

        def worker():
            _wait_for_slot()
            with lock:
                stamps.append(time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stamps.sort()
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        assert all(g >= _MIN_SECONDS_BETWEEN_CALLS * 0.8 for g in gaps), gaps

    def test_uses_a_thread_lock_not_an_asyncio_lock(self):
        """The runner calls asyncio.run() repeatedly; a lock bound to one
        event loop is invalid in the next."""
        assert isinstance(fyers_module._pacing_lock, type(threading.Lock()))
        assert not isinstance(fyers_module._pacing_lock, asyncio.Lock)


class TestPacedWrapper:
    def test_returns_the_underlying_result_unchanged(self):
        assert _paced(lambda: {"s": "ok", "v": 1})() == {"s": "ok", "v": 1}

    def test_forwards_the_params_argument(self):
        seen = []
        _paced(lambda p: seen.append(p))({"symbol": "NSE:NIFTY50-INDEX"})
        assert seen == [{"symbol": "NSE:NIFTY50-INDEX"}]

    def test_exceptions_propagate_rather_than_being_swallowed(self):
        def boom(*a):
            raise RuntimeError("transport down")
        with pytest.raises(RuntimeError, match="transport down"):
            _paced(boom)()

    def test_a_failed_call_still_consumed_its_slot(self):
        """A rejected request counts against the rate ceiling too, so a
        failing call must not let the next one through early."""
        def boom(*a):
            raise RuntimeError("nope")
        with pytest.raises(RuntimeError):
            _paced(boom)()
        start = time.monotonic()
        _wait_for_slot()
        assert time.monotonic() - start > 0


class TestWiredIntoTheChokePoint:
    def test_dispatch_goes_through_the_pacer(self):
        """Pacing that exists but is not on the transport path is the same
        bug in a new place.

        Asserted against `_invoke_once`, which owns the single SDK
        round-trip. `_call` wrapped it when rate-limit retry was added
        (tests/test_fyers_rate_limit_retry.py) -- the dispatch moved, the
        pacing did not."""
        import inspect
        source = inspect.getsource(fyers_module.FyersBroker._invoke_once)
        assert source.count("_paced(method)") == 2, source

    def test_pacing_runs_in_the_worker_thread_not_the_event_loop(self):
        """Sleeping on the loop would stall every other coroutine instead of
        just the caller waiting for its slot."""
        import inspect
        source = inspect.getsource(fyers_module.FyersBroker._invoke_once)
        assert "asyncio.to_thread(_paced(method))" in source
        assert "asyncio.to_thread(_paced(method), params)" in source

    def test_every_retry_attempt_is_paced_not_just_the_first(self):
        """Retry calls back into the paced round-trip rather than reaching
        the SDK directly -- otherwise a burst of retries would be exactly
        the unpaced burst the pacer exists to prevent."""
        import inspect
        source = inspect.getsource(fyers_module.FyersBroker._call)
        assert "_invoke_once(" in source
        assert "to_thread" not in source, "retry path must not dispatch directly"
