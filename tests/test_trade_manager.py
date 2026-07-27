"""Trade Manager tests — straddle / premium-VWAP variant."""
from datetime import datetime

import pytest

from bujji.core.enums import Decision, Direction, Side
from bujji.core.models import OptionContract, OptionType, Position
from bujji.trade.manager import TradeManager
from tests.conftest import c


def make_straddle_position(combined_entry: float = 240.0):
    ce = OptionContract("NIFTY22000CE", "NIFTY", 22000, OptionType.CE, "WEEKLY", 75)
    pe = OptionContract("NIFTY22000PE", "NIFTY", 22000, OptionType.PE, "WEEKLY", 75)
    return Position(
        contract=ce,
        ce_contract=ce,
        pe_contract=pe,
        direction=Direction.NEUTRAL,
        entry_side=Side.SELL,
        quantity=75,
        entry_price=combined_entry,
        entry_spot=22000.0,
        entry_time=datetime(2026, 7, 5, 9, 20),
        orb=None,
    )


def test_hold_when_premium_below_vwap(config, logger):
    """Premium decays below VWAP → seller winning → HOLD."""
    tm = TradeManager(config, logger)
    pos = make_straddle_position(combined_entry=240.0)
    tm.open_position(pos, c(9, 20, 22000, 22010, 21990, 22005))

    # Entry seeds VWAP at 240.  Next candle: premium = 230 < 240 → HOLD.
    d = tm.reassess(c(9, 25, 22000, 22010, 21990, 22005), combined_premium=230.0)
    assert d.decision is Decision.HOLD
    assert tm._consecutive_above == 0  # noqa: SLF001


def test_exit_on_first_candle_close_above_vwap(config, logger):
    """A SINGLE candle close above premium VWAP -> immediate EXIT (changed
    from the original 2-consecutive-closes rule per explicit operator
    request -- see docs/AUDIT_LOG.md)."""
    tm = TradeManager(config, logger)
    pos = make_straddle_position(combined_entry=240.0)
    tm.open_position(pos, c(9, 20, 22000, 22010, 21990, 22005))
    # VWAP = 240 after seed. First candle above VWAP -> EXIT immediately,
    # no second confirming candle needed.
    d1 = tm.reassess(c(9, 25, 22000, 22010, 21990, 22005), combined_premium=250.0)
    assert d1.decision is Decision.EXIT
    assert "1_candle_close_above_vwap" in d1.reason
    assert tm._consecutive_above == 1  # noqa: SLF001


def test_hold_persists_while_premium_stays_at_or_below_vwap(config, logger):
    """Multiple candles at/below VWAP must keep holding -- only a close
    STRICTLY above VWAP triggers the exit."""
    tm = TradeManager(config, logger)
    pos = make_straddle_position(combined_entry=240.0)
    tm.open_position(pos, c(9, 20, 22000, 22010, 21990, 22005))

    d1 = tm.reassess(c(9, 25, 22000, 22010, 21990, 22005), combined_premium=235.0)
    assert d1.decision is Decision.HOLD
    assert tm._consecutive_above == 0  # noqa: SLF001

    d2 = tm.reassess(c(9, 30, 22000, 22010, 21990, 22005), combined_premium=230.0)
    assert d2.decision is Decision.HOLD
    assert tm._consecutive_above == 0  # noqa: SLF001

    # Now it crosses above -> immediate exit, no second confirming candle.
    d3 = tm.reassess(c(9, 35, 22000, 22010, 21990, 22005), combined_premium=250.0)
    assert d3.decision is Decision.EXIT
    assert tm._consecutive_above == 1  # noqa: SLF001


def test_exit_on_max_loss(config, logger):
    """Risk stop: MTM loss exceeds max_mtm_loss → EXIT regardless of VWAP."""
    config.risk.max_mtm_loss = 300  # 4 pts * 75 = 300.
    tm = TradeManager(config, logger)
    pos = make_straddle_position(combined_entry=240.0)
    tm.open_position(pos, c(9, 20, 22000, 22010, 21990, 22005))

    # premium rises by 5 → mtm = (240-245)*75 = -375 < -300 → risk fails.
    d = tm.reassess(c(9, 25, 22000, 22010, 21990, 22005), combined_premium=245.0)
    assert d.decision is Decision.EXIT
    assert "risk" in d.reason


def test_exit_on_hard_exit_time(config, logger):
    """Hard stop at configured hard_exit time."""
    tm = TradeManager(config, logger)
    pos = make_straddle_position()
    tm.open_position(pos, c(9, 20, 22000, 22010, 21990, 22005))

    # 15:05 = hard_exit.
    d = tm.reassess(c(15, 5, 22000, 22010, 21990, 22005), combined_premium=230.0)
    assert d.decision is Decision.EXIT
    assert "hard_exit_time" in d.reason
