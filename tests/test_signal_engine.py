"""Signal Engine tests — straddle variant (fires at 09:20, no ORB)."""
from bujji.core.enums import SignalType
from bujji.signal.engine import SignalEngine
from tests.conftest import c


def test_no_trade_before_trading_start(config, logger):
    """Candles before 09:20 must not generate a signal."""
    eng = SignalEngine(config, logger)
    sig = eng.on_candle(c(9, 15, 22000, 22010, 21990, 22005))
    assert sig.type is SignalType.NO_TRADE
    assert sig.reason == "before_trading_start"


def test_enter_straddle_at_trading_start(config, logger):
    """First candle at or after 09:20 emits ENTER_STRADDLE."""
    eng = SignalEngine(config, logger)
    sig = eng.on_candle(c(9, 20, 22000, 22010, 21990, 22005))
    assert sig.type is SignalType.ENTER_STRADDLE
    assert sig.is_trade
    assert sig.spot == 22005


def test_orb_ready_always_true(config, logger):
    """No ORB is needed — orb_ready is always True from the first candle."""
    eng = SignalEngine(config, logger)
    assert eng.orb_ready is True
    eng.on_candle(c(9, 15, 22000, 22010, 21990, 22005))
    assert eng.orb_ready is True


def test_one_signal_per_day(config, logger):
    """Only the first 09:20 candle fires; subsequent candles are NO_TRADE."""
    eng = SignalEngine(config, logger)
    first = eng.on_candle(c(9, 20, 22000, 22010, 21990, 22005))
    second = eng.on_candle(c(9, 25, 22005, 22015, 21995, 22010))
    assert first.is_trade
    assert second.type is SignalType.NO_TRADE
    assert second.reason == "already_signalled"


def test_no_trade_at_or_after_hard_exit(config, logger):
    """Candles at or after hard_exit must not trigger a signal."""
    eng = SignalEngine(config, logger)
    sig = eng.on_candle(c(15, 5, 22000, 22010, 21990, 22005))
    assert sig.type is SignalType.NO_TRADE
