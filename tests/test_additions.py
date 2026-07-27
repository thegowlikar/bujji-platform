"""Tests for the infrastructure additions — updated for the straddle strategy.

Covers EventBus, Decision Trace (TradeManager), and Replay Engine.
Trade Thesis and MarketBrain tests from the ORB strategy have been removed;
the straddle strategy enters unconditionally and carries no directional thesis.
"""
import asyncio

import pytest

from bujji.core.event_bus import Event, EventBus, EventType
from bujji.core.enums import Decision
from bujji.replay.engine import ReplayEngine
from bujji.trade.manager import TradeManager
from tests.conftest import c
from tests.test_trade_manager import make_straddle_position as make_position  # re-export

__all__ = ["make_position"]


# 1. Decision Trace ----------------------------------------------------- #
def test_decision_trace_on_hold_and_exit(config, logger):
    tm = TradeManager(config, logger)
    pos = make_position(combined_entry=240.0)
    tm.open_position(pos, c(9, 20, 22000, 22010, 21990, 22005))

    # premium stays below VWAP → HOLD
    hold = tm.reassess(c(9, 25, 22000, 22010, 21990, 22005), combined_premium=230.0)
    assert hold.trace is not None and hold.trace.conclusion == "HOLD"
    assert not hold.trace.failed_checks

    # Two consecutive above VWAP → EXIT on second candle
    tm2 = TradeManager(config, logger)
    tm2.open_position(make_position(240.0), c(9, 20, 22000, 22010, 21990, 22005))
    tm2.reassess(c(9, 25, 22000, 22010, 21990, 22005), combined_premium=260.0)
    ex = tm2.reassess(c(9, 30, 22000, 22010, 21990, 22005), combined_premium=265.0)
    assert ex.trace.conclusion == "EXIT"
    assert ex.trace.failed_checks
    assert "trade_manager" in ex.trace.render()


# 2. Event Bus ---------------------------------------------------------- #
@pytest.mark.asyncio
async def test_event_bus_pubsub_and_isolation():
    bus = EventBus()
    seen = []
    bus.subscribe(EventType.CANDLE_CLOSED, lambda e: seen.append(e.payload))

    async def async_handler(e):
        seen.append("async")
    bus.subscribe(EventType.CANDLE_CLOSED, async_handler)

    def boom(e):
        raise RuntimeError("handler failure")
    bus.subscribe(EventType.CANDLE_CLOSED, boom)  # Must not break others.

    await bus.publish(Event(EventType.CANDLE_CLOSED, {"x": 1}))
    assert {"x": 1} in seen and "async" in seen


# 3. Replay Engine ------------------------------------------------------ #
@pytest.mark.asyncio
async def test_replay_matches_live_path(config, logger, tmp_path):
    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"

    candles = [
        c(9, 20, 22000, 22010, 21990, 22005),   # straddle entry
        c(9, 25, 22000, 22010, 21990, 22005),   # premium ≈ VWAP, HOLD
        c(15, 5, 22000, 22010, 21990, 22005),   # hard_exit → square off
    ]
    engine = ReplayEngine(config, logger)
    result = await engine.run(candles)

    assert result.candles_processed == 3
    assert result.final_state == "DONE_FOR_DAY"
    assert len(result.trades) == 1
    assert any("POSITION_OPENED" in e for e in result.events)
    assert any("POSITION_CLOSED" in e for e in result.events)
