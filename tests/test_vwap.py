"""VWAP tests — verify real volume-weighting and the safety guard.

FYERS supplies real per-candle volume for NIFTY spot via the historical
endpoint, so VWAP must be a true volume-weighted VWAP. The equal-weight
approximation is a disabled-by-default fallback; without volume and without the
fallback, the engine must refuse to trade.
"""
from bujji.core.enums import SignalType
from bujji.signal.engine import SignalEngine
from bujji.signal.indicators import VwapTracker
from tests.conftest import c


def test_volume_weighted_vwap_is_real():
    vt = VwapTracker(allow_equal_weight_fallback=False)
    # Two candles: heavy volume near 200, light near 100 -> weighted toward 200.
    vt.update(c(9, 15, 200, 200, 200, 200, vol=900))
    vt.update(c(9, 20, 100, 100, 100, 100, vol=100))
    assert vt.is_real and vt.ready
    # (200*900 + 100*100) / 1000 = 190.
    assert abs(vt.value - 190.0) < 1e-6


def test_no_volume_without_fallback_is_not_ready():
    vt = VwapTracker(allow_equal_weight_fallback=False)
    vt.update(c(9, 15, 100, 110, 90, 105, vol=0))
    assert not vt.is_real
    assert not vt.ready
    assert vt.value == 0.0
    assert vt.zero_volume_candles == 1


def test_fallback_when_explicitly_enabled():
    vt = VwapTracker(allow_equal_weight_fallback=True)
    vt.update(c(9, 15, 100, 120, 90, 105, vol=0))  # typical = 105.
    assert not vt.is_real  # Still flagged as approximated.
    assert vt.ready
    assert abs(vt.value - 105.0) < 1e-6


def test_engine_refuses_trade_without_real_vwap(config, logger):
    config.market.vwap_equal_weight_fallback = False
    eng = SignalEngine(config, logger)
    # Zero-volume candles: ORB builds, but a breakout must NOT signal.
    eng.on_candle(c(9, 15, 100, 110, 90, 105, vol=0))
    sig = eng.on_candle(c(9, 20, 111, 130, 110, 129, vol=0))
    assert sig.type is SignalType.NO_TRADE
    assert sig.reason == "vwap_not_ready"


def test_engine_trades_with_real_volume(config, logger):
    config.market.vwap_equal_weight_fallback = False
    eng = SignalEngine(config, logger)
    eng.on_candle(c(9, 15, 100, 110, 90, 105, vol=1000))
    sig = eng.on_candle(c(9, 20, 111, 130, 110, 129, vol=1000))
    assert sig.is_trade
    assert eng.vwap_is_real
