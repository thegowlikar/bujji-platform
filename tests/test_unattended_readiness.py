"""Pre-unattended-trading verification.

Targeted tests for the four areas explicitly called out before letting the
system trade unattended:
  1. Order execution fidelity — see docs/UNATTENDED_READINESS.md for the
     documentation half of this (no code changes needed there).
  2. Restart recovery — including a real gap found and fixed during this
     pass: a resumed position now re-triggers the Tick Engine.
  3. Risk kill switches — WebSocket loss, REST loss, and the "two independent
     triggers try to exit at once" scenario the Tick Engine introduced.
  4. Multi-day stability — not something a unit test can prove; see the
     documentation for the operational plan instead.
"""
import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.enums import State
from bujji.tick.engine import TickEngine
from tests.test_tick_engine import FakeTickFeed
from tests.test_tier1_capital_protection import build_orch, _enter_position, _fast_broker_cfg


# ---------------------------------------------------------------------- #
# 2. Restart recovery — including Tick Engine continuity
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_recovery_resumes_tick_monitoring_not_just_candle_monitoring(
    config, logger, tmp_path
):
    """The gap found in this pass: a resumed position must re-arm the Tick
    Engine's WebSocket subscription, not silently fall back to candle-only
    monitoring for the rest of the day."""
    _fast_broker_cfg(config)
    broker = PaperBroker()  # Shared across the simulated restart.

    a, status_a = build_orch(config, logger, broker, tmp_path)
    feed_a = FakeTickFeed()
    TickEngine(a, feed_a, config, logger, a.bus, status_a)
    await a.startup()
    await _enter_position(a)
    assert a.state is State.IN_POSITION
    symbol = a.position.contract.symbol

    # Simulate a crash + restart: brand-new orchestrator and Tick Engine,
    # same broker + session store (same pattern as the existing C1 tests).
    b, status_b = build_orch(config, logger, broker, tmp_path)
    feed_b = FakeTickFeed()
    tick_engine_b = TickEngine(b, feed_b, config, logger, b.bus, status_b)
    await b.startup()

    assert b.state is State.IN_POSITION  # C1: position resumed (already proven).
    assert feed_b.started  # NEW: the tick feed was (re)started on recovery.
    assert symbol in feed_b.subscribed  # NEW: re-subscribed to the same contract.
    assert tick_engine_b._task is not None  # noqa: SLF001 - monitor loop restarted.

    # And the resumed tick monitoring actually works: a stop-loss breach on a
    # tick still triggers a clean exit through the normal path.
    config.risk.max_mtm_loss = 500
    feed_b.set_price(symbol, b.position.entry_price + 100)
    await tick_engine_b._check_once(symbol)  # noqa: SLF001
    assert b.state is State.DONE_FOR_DAY
    assert not b.has_open_position()


@pytest.mark.asyncio
async def test_recovery_does_not_duplicate_journal_entries(config, logger, tmp_path):
    """Publishing POSITION_OPENED again on recovery must not cause any
    duplicate order placement or journal entry — it's purely an event for
    observers (Tick Engine, logging) to react to."""
    _fast_broker_cfg(config)
    broker = PaperBroker()
    a, status_a = build_orch(config, logger, broker, tmp_path)
    await a.startup()
    await _enter_position(a)

    b, status_b = build_orch(config, logger, broker, tmp_path)
    await b.startup()
    assert b.state is State.IN_POSITION
    assert broker.place_calls == 1  # Recovery re-publishes an event, not an order.

    ok = await b.square_off("test_cleanup")
    assert ok
    assert len(b._journal.all_trades()) == 1  # noqa: SLF001 - exactly one trade.


# ---------------------------------------------------------------------- #
# 3. Risk kill switches
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_websocket_loss_falls_back_to_candle_only_monitoring_safely(
    config, logger, tmp_path
):
    """If the tick feed disconnects, the Tick Engine must not crash and must
    not falsely trigger an exit on stale/missing data — the existing
    candle-driven risk check (untouched) remains the safety net."""
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    feed = FakeTickFeed()
    engine = TickEngine(orch, feed, config, logger, orch.bus, status)
    await orch.startup()
    await _enter_position(orch)

    feed.is_connected = False  # Simulate WS drop — no ticks arrive.
    await engine._check_once(orch.position.contract.symbol)  # noqa: SLF001

    assert orch.state is State.IN_POSITION  # No crash, no false exit.
    # The existing candle-driven path (unchanged) still catches a real breach
    # on the next candle regardless of tick-feed state.
    config.risk.max_mtm_loss = 1  # Force the candle-driven check to trip.
    from tests.conftest import c
    await orch.on_candle(c(9, 25, 22078, 22079, 21950, 21951, vol=1000))
    assert orch.state is State.DONE_FOR_DAY


@pytest.mark.asyncio
async def test_transient_rest_failure_during_exit_recovers_via_existing_retry(
    config, logger, tmp_path
):
    """A single transient failure must not need operator intervention — the
    existing retry/backoff (C3) already recovers it automatically."""
    _fast_broker_cfg(config)

    class FlakyBroker(PaperBroker):
        def __init__(self):
            super().__init__()
            self.fail_next = False

        async def place_order(self, request):
            if self.fail_next and request.side.value == "BUY":  # Exit leg.
                self.fail_next = False  # Only the first attempt fails.
                raise RuntimeError("simulated transient REST/network blip")
            return await super().place_order(request)

    broker = FlakyBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await _enter_position(orch)

    broker.fail_next = True
    ok = await orch.square_off("test_forced_exit")
    assert ok is True  # Recovered automatically via the existing retry.
    assert not orch.has_open_position()


@pytest.mark.asyncio
async def test_persistent_rest_connectivity_loss_preserves_position(
    config, logger, tmp_path
):
    """A REST/network outage that outlasts the retry budget must never leave
    the system believing it's flat when it isn't (C4, re-verified here)."""
    _fast_broker_cfg(config)

    class DownBroker(PaperBroker):
        async def place_order(self, request):
            if request.side.value == "BUY":  # Exit leg — every attempt fails.
                raise RuntimeError("simulated persistent REST/network outage")
            return await super().place_order(request)

    broker = DownBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await _enter_position(orch)
    assert orch.has_open_position()

    ok = await orch.square_off("test_forced_exit")
    assert ok is False  # Exit failed — must not report success.
    assert orch.has_open_position()  # Position preserved, not silently lost.
    assert status.healthy is False

    # Connectivity "recovers" (swap back to a working exit path) — retry
    # from a fresh call succeeds and genuinely flattens.
    broker.place_order = PaperBroker.place_order.__get__(broker, PaperBroker)
    ok2 = await orch.square_off("test_forced_exit_retry")
    assert ok2 is True
    assert not orch.has_open_position()


@pytest.mark.asyncio
async def test_concurrent_tick_and_candle_exit_triggers_do_not_duplicate(
    config, logger, tmp_path
):
    """The scenario the Tick Engine specifically introduces: a tick-driven
    stop-loss and the candle-driven exit check could both decide to exit in
    the same window. Only one exit order may ever be placed."""
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    feed = FakeTickFeed()
    engine = TickEngine(orch, feed, config, logger, orch.bus, status)
    await orch.startup()
    await _enter_position(orch)

    config.risk.max_mtm_loss = 1  # Trivial cap so both paths want to exit.
    feed.set_price(orch.position.contract.symbol, orch.position.entry_price + 50)

    from tests.conftest import c
    # Fire the tick check and the candle check "at the same time" (sequentially
    # within one test, since both ultimately serialize on the single event
    # loop exactly as they would in production).
    await engine._check_once(orch.position.contract.symbol)  # noqa: SLF001
    assert orch.state is State.DONE_FOR_DAY
    buy_orders_before = broker.place_calls

    await orch.on_candle(c(9, 25, 22078, 22079, 21950, 21951, vol=1000))
    assert broker.place_calls == buy_orders_before  # No second exit order placed.
    assert len(orch._journal.all_trades()) == 1  # noqa: SLF001
