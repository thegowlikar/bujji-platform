from datetime import datetime

from bujji.core.enums import Decision, Direction, Side
from bujji.core.models import OpeningRange, OptionContract, OptionType, Position
from bujji.trade.manager import TradeManager
from tests.conftest import c


def make_position(direction=Direction.BULLISH):
    contract = OptionContract("NIFTY22000PE", "NIFTY", 22000, OptionType.PE,
                              "WEEKLY", 75)
    orb = OpeningRange(110, 90, datetime(2026, 7, 5, 9, 15),
                       datetime(2026, 7, 5, 9, 20))
    return Position(contract, direction, Side.SELL, 75, 120.0, 22050.0,
                    datetime(2026, 7, 5, 9, 20), orb)


def test_hold_when_thesis_intact(config, logger):
    tm = TradeManager(config, logger)
    pos = make_position()
    tm.open_position(pos, c(9, 20, 111, 130, 110, 129))
    # Higher close, above VWAP, premium decaying -> profit.
    d = tm.reassess(c(9, 25, 129, 135, 128, 134), vwap=120.0, current_premium=110.0)
    assert d.decision is Decision.HOLD


def test_exit_on_vwap_loss(config, logger):
    tm = TradeManager(config, logger)
    pos = make_position()
    tm.open_position(pos, c(9, 20, 111, 130, 110, 129))
    # Close below VWAP -> trend + would_reenter fail.
    d = tm.reassess(c(9, 25, 128, 129, 100, 105), vwap=120.0, current_premium=130.0)
    assert d.decision is Decision.EXIT
    assert "trend" in d.reason or "would_reenter" in d.reason


def test_exit_on_max_loss(config, logger):
    config.risk.max_mtm_loss = 300  # 4 points * 75 = 300 loss cap.
    tm = TradeManager(config, logger)
    pos = make_position()
    tm.open_position(pos, c(9, 20, 111, 130, 110, 129))
    # Above VWAP but premium exploded -> risk check fails.
    d = tm.reassess(c(9, 25, 129, 135, 128, 134), vwap=120.0, current_premium=130.0)
    assert d.decision is Decision.EXIT
    assert "risk" in d.reason


def test_exit_on_aggressive_reversal(config, logger):
    tm = TradeManager(config, logger)
    pos = make_position()
    tm.open_position(pos, c(9, 20, 111, 130, 110, 129))
    # Big bearish candle but still above VWAP -> control check fails.
    d = tm.reassess(c(9, 25, 140, 141, 125, 126), vwap=120.0, current_premium=115.0)
    assert d.decision is Decision.EXIT
    assert "control" in d.reason
