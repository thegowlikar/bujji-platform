from bujji.core.enums import Direction, SignalType
from bujji.signal.engine import SignalEngine
from tests.conftest import c


def build_orb(engine):
    # ORB window 9:15-9:20 -> single 5m candle at 9:15.
    engine.on_candle(c(9, 15, 100, 110, 90, 105))


def test_no_trade_before_orb(config, logger):
    eng = SignalEngine(config, logger)
    sig = eng.on_candle(c(9, 15, 100, 110, 90, 105))
    assert sig.type is SignalType.NO_TRADE
    assert eng.orb_ready


def test_bullish_breakout(config, logger):
    eng = SignalEngine(config, logger)
    build_orb(eng)
    # Strong bullish candle closing above ORB high (110) and above VWAP.
    sig = eng.on_candle(c(9, 20, 111, 130, 110, 129))
    assert sig.is_trade
    assert sig.direction is Direction.BULLISH


def test_bearish_breakdown(config, logger):
    eng = SignalEngine(config, logger)
    build_orb(eng)
    sig = eng.on_candle(c(9, 20, 89, 90, 60, 61))
    assert sig.is_trade
    assert sig.direction is Direction.BEARISH


def test_weak_body_rejected(config, logger):
    eng = SignalEngine(config, logger)
    build_orb(eng)
    # Closes above ORB high but body is a small fraction of the range.
    sig = eng.on_candle(c(9, 20, 111, 140, 100, 112))
    assert sig.type is SignalType.NO_TRADE


def test_one_signal_per_day(config, logger):
    eng = SignalEngine(config, logger)
    build_orb(eng)
    first = eng.on_candle(c(9, 20, 111, 130, 110, 129))
    second = eng.on_candle(c(9, 25, 130, 150, 129, 149))
    assert first.is_trade
    assert second.type is SignalType.NO_TRADE
