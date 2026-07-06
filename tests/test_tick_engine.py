"""Tick Engine — continuous, tick-driven risk monitoring.

Verifies: entries remain untouched (no interaction with SignalEngine at all),
a stop-loss/profit-target breach on a tick triggers an immediate exit via the
existing square_off() path (not a new exit mechanism), the tick monitor stops
cleanly when the position closes, and a broker with no live tick credentials
simply gets no tick monitoring (no crash, no fake data).
"""
import asyncio

import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.enums import State
from bujji.tick.engine import TickEngine
from tests.test_tier1_capital_protection import build_orch, _enter_position, _fast_broker_cfg


class FakeTickFeed:
    """Duck-typed stand-in for FyersTickFeed — no real thread/network."""

    def __init__(self):
        self.started = False
        self.subscribed: list[str] = []
        self._prices: dict[str, float] = {}
        self.is_connected = True
        self.connect_count = 1
        self.last_error = None

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def subscribe(self, symbols):
        self.subscribed.extend(symbols)

    def set_price(self, symbol, price):
        self._prices[symbol] = price

    def latest(self, symbol):
        return self._prices.get(symbol)

    def tick_age_seconds(self, symbol):
        return 0.5 if symbol in self._prices else None


async def _open_position_with_tick_engine(config, logger, tmp_path, feed=None):
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    engine = TickEngine(orch, feed, config, logger, orch.bus, status)
    await orch.startup()
    await _enter_position(orch)
    return orch, status, engine


@pytest.mark.asyncio
async def test_no_feed_means_no_tick_monitoring(config, logger, tmp_path):
    """A broker with no live tick credentials (e.g. plain paper mode) must
    not crash or fabricate any tick data — it simply gets nothing."""
    orch, status, engine = await _open_position_with_tick_engine(
        config, logger, tmp_path, feed=None
    )
    assert orch.state is State.IN_POSITION  # Entry untouched by the tick engine.
    assert engine._task is None  # noqa: SLF001 - never started, no feed.


@pytest.mark.asyncio
async def test_stop_loss_breach_triggers_immediate_exit(config, logger, tmp_path):
    feed = FakeTickFeed()
    config.risk.max_mtm_loss = 500  # Small cap so a modest premium rise breaches it.
    orch, status, engine = await _open_position_with_tick_engine(
        config, logger, tmp_path, feed=feed
    )
    assert engine._task is not None  # noqa: SLF001 - started on POSITION_OPENED.
    assert feed.started and feed.subscribed == [orch.position.contract.symbol]

    entry_premium = orch.position.entry_price
    # Premium rose enough that MTM = (entry - current) * qty breaches -500.
    feed.set_price(orch.position.contract.symbol, entry_premium + 100)

    await engine._check_once(orch.position.contract.symbol)  # noqa: SLF001

    assert orch.state is State.DONE_FOR_DAY
    assert not orch.has_open_position()
    trades = orch._journal.all_trades()  # noqa: SLF001
    assert len(trades) == 1
    assert "tick_stop_loss" in trades[0]["exit_reason"]


@pytest.mark.asyncio
async def test_profit_target_is_disabled_by_default(config, logger, tmp_path):
    """max_mtm_profit defaults to None — a big favourable move must NOT exit
    the position unless the operator explicitly opted in."""
    feed = FakeTickFeed()
    assert config.risk.max_mtm_profit is None
    orch, status, engine = await _open_position_with_tick_engine(
        config, logger, tmp_path, feed=feed
    )
    entry_premium = orch.position.entry_price
    feed.set_price(orch.position.contract.symbol, max(0.5, entry_premium - 1000))

    await engine._check_once(orch.position.contract.symbol)  # noqa: SLF001

    assert orch.state is State.IN_POSITION  # Untouched — no profit target configured.


@pytest.mark.asyncio
async def test_profit_target_triggers_when_explicitly_configured(config, logger, tmp_path):
    feed = FakeTickFeed()
    config.risk.max_mtm_profit = 200  # Opt-in.
    orch, status, engine = await _open_position_with_tick_engine(
        config, logger, tmp_path, feed=feed
    )
    entry_premium = orch.position.entry_price
    qty = orch.position.quantity
    # mtm = (entry - current) * qty >= 200.
    feed.set_price(orch.position.contract.symbol, max(0.5, entry_premium - (200 / qty) - 1))

    await engine._check_once(orch.position.contract.symbol)  # noqa: SLF001

    assert orch.state is State.DONE_FOR_DAY
    trades = orch._journal.all_trades()  # noqa: SLF001
    assert "tick_profit_target" in trades[0]["exit_reason"]


@pytest.mark.asyncio
async def test_no_tick_yet_is_a_safe_noop(config, logger, tmp_path):
    feed = FakeTickFeed()  # No price set for the symbol.
    orch, status, engine = await _open_position_with_tick_engine(
        config, logger, tmp_path, feed=feed
    )
    await engine._check_once(orch.position.contract.symbol)  # noqa: SLF001
    assert orch.state is State.IN_POSITION  # No tick -> candle-driven backstop remains.


@pytest.mark.asyncio
async def test_position_closed_cancels_the_monitor_task(config, logger, tmp_path):
    feed = FakeTickFeed()
    orch, status, engine = await _open_position_with_tick_engine(
        config, logger, tmp_path, feed=feed
    )
    task = engine._task  # noqa: SLF001
    assert task is not None and not task.done()
    await orch.square_off("manual_test_exit")
    await asyncio.sleep(0)  # Let the scheduled cancellation actually propagate.
    assert task.cancelled() or task.done()
    assert status.tick_mtm is None  # Reset on close.
