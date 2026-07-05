"""Tests for the five infrastructure additions.

These verify the additions *describe and replay* the existing behaviour without
changing any trading rule.
"""
import asyncio

import pytest

from bujji.core.event_bus import Event, EventBus, EventType
from bujji.core.enums import Direction, Decision
from bujji.market.brain import MarketBrain
from bujji.signal.engine import SignalEngine
from bujji.trade.manager import TradeManager
from bujji.replay.engine import ReplayEngine
from tests.conftest import c
from tests.test_trade_manager import make_position


# 1. Trade Thesis ------------------------------------------------------- #
def test_thesis_captured_on_signal(config, logger):
    eng = SignalEngine(config, logger)
    eng.on_candle(c(9, 15, 100, 110, 90, 105))
    sig = eng.on_candle(c(9, 20, 111, 130, 110, 129))
    assert sig.thesis is not None
    assert sig.thesis.direction is Direction.BULLISH
    assert "BULLISH" in sig.thesis.narrative
    assert sig.thesis.as_conditions_dict()["above_vwap"] == "PASS"


# 2. Decision Trace ----------------------------------------------------- #
def test_decision_trace_on_hold_and_exit(config, logger):
    tm = TradeManager(config, logger)
    tm.open_position(make_position(), c(9, 20, 111, 130, 110, 129))
    hold = tm.reassess(c(9, 25, 129, 135, 128, 134), 120.0, 110.0)
    assert hold.trace is not None and hold.trace.conclusion == "HOLD"
    assert not hold.trace.failed_checks

    tm2 = TradeManager(config, logger)
    tm2.open_position(make_position(), c(9, 20, 111, 130, 110, 129))
    ex = tm2.reassess(c(9, 25, 128, 129, 95, 96), 120.0, 130.0)
    assert ex.trace.conclusion == "EXIT"
    assert ex.trace.failed_checks
    assert "trade_manager" in ex.trace.render()


# 3. Market Brain ------------------------------------------------------- #
def test_market_brain_direction_neutral(config, logger):
    from bujji.core.models import OpeningRange
    from datetime import datetime

    brain = MarketBrain(config.risk.breakout_body_ratio)
    orb = OpeningRange(110, 90, datetime(2026, 7, 5, 9, 15),
                       datetime(2026, 7, 5, 9, 20))
    s = brain.interpret(c(9, 20, 111, 130, 110, 129), 120.0, orb)
    assert s.above_vwap and s.above_orb_high and s.strong_body
    assert s.aggressive_bullish and not s.aggressive_bearish


def test_signal_and_manager_share_brain_reading(config, logger):
    # Would-reenter mirrors the entry test: same conditions => still valid.
    eng = SignalEngine(config, logger)
    eng.on_candle(c(9, 15, 100, 110, 90, 105))
    sig = eng.on_candle(c(9, 20, 111, 130, 110, 129))
    assert sig.is_trade
    # Re-presenting the same candle to evaluate_entry still qualifies.
    assert eng.evaluate_entry(c(9, 25, 111, 130, 110, 129), 120.0, sig.orb) \
        is Direction.BULLISH


# 4. Event Bus ---------------------------------------------------------- #
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


# 5. Replay Engine ------------------------------------------------------ #
@pytest.mark.asyncio
async def test_replay_matches_live_path(config, logger, tmp_path):
    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"

    candles = [
        c(9, 15, 22000, 22010, 21990, 22005),   # ORB
        c(9, 20, 22006, 22080, 22005, 22079),   # strong bullish breakout
        c(9, 25, 22078, 22079, 21950, 21951),   # loses VWAP -> exit
    ]
    engine = ReplayEngine(config, logger)
    result = await engine.run(candles)

    assert result.candles_processed == 3
    assert result.final_state == "DONE_FOR_DAY"
    assert len(result.trades) == 1
    assert any("POSITION_OPENED" in e for e in result.events)
    assert any("POSITION_CLOSED" in e for e in result.events)
    assert result.trades[0]["thesis"]  # Thesis persisted through replay.
