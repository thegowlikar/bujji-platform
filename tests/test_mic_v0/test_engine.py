"""Phase 20.1 -- mic_v0.engine tests. Synthetic fixtures only."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from bujji.core.models import Candle
from bujji.intelligence.context import EXECUTION_MODE_HISTORICAL_REPLAY, IntelligenceContext
from bujji.intelligence.event_brain import VIX_ELEVATED_THRESHOLD
from bujji.mic_v0.engine import compose_market_state
from bujji.mic_v0.models import (
    CONFIDENCE_LOW,
    CONFIDENCE_NONE,
    ENV_MEAN_REVERSION,
    ENV_NO_TRADE,
    ENV_TREND_FOLLOWING,
    EVENT_CONTEXT_NOT_AVAILABLE,
    REGIME_RANGE,
    REGIME_TREND,
    RISK_EXTREME,
)
from bujji.mic_v0.risk_classifier import VIX_EXTREME_THRESHOLD

BASE_TIME = datetime(2026, 1, 5, 9, 15)


def _candles(prices):
    return [
        Candle(timestamp=BASE_TIME + timedelta(minutes=5 * i), open=p, high=p + 0.5, low=p - 0.5, close=p)
        for i, p in enumerate(prices)
    ]


def _context(as_of=None):
    return IntelligenceContext(as_of_time=as_of or BASE_TIME, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY)


def _long_vix_history(level=14.0, n=100):
    return [level] * n


def test_trending_series_classified_trend_following():
    prices = [100.0 + i * 0.9 for i in range(30)]
    state = compose_market_state(_candles(prices), current_vix=14.0, trailing_vix_history=_long_vix_history(), context=_context())
    assert state.market_regime == REGIME_TREND
    assert state.recommended_environment == ENV_TREND_FOLLOWING


def test_choppy_series_classified_mean_reversion():
    # A genuine oscillation (real amplitude, returns to the mean every
    # cycle, stable variance throughout -- unlike a strict +/-
    # alternation, which trips RegimeBrain's own compression/expansion
    # checks before ever reaching the efficiency-ratio comparison and
    # correctly resolves to UNCLEAR, not RANGE).
    prices = [100.0 + 0.3 * math.sin(i * (2 * math.pi / 8)) for i in range(40)]
    state = compose_market_state(_candles(prices), current_vix=14.0, trailing_vix_history=_long_vix_history(), context=_context())
    assert state.market_regime == REGIME_RANGE
    assert state.recommended_environment == ENV_MEAN_REVERSION


def test_extreme_vix_forces_no_trade_even_during_a_trend():
    prices = [100.0 + i * 0.9 for i in range(30)]
    state = compose_market_state(
        _candles(prices), current_vix=VIX_EXTREME_THRESHOLD + 5.0,
        trailing_vix_history=_long_vix_history(), context=_context(),
    )
    assert state.risk_state == RISK_EXTREME
    assert state.recommended_environment == ENV_NO_TRADE


def test_insufficient_vix_history_produces_no_trade_and_insufficient_quality():
    prices = [100.0 + i * 0.9 for i in range(30)]
    state = compose_market_state(_candles(prices), current_vix=14.0, trailing_vix_history=[14.0] * 5, context=_context())
    assert state.recommended_environment == ENV_NO_TRADE
    assert state.data_quality == "INSUFFICIENT"


def test_event_context_always_not_available():
    prices = [100.0 + i * 0.9 for i in range(30)]
    state = compose_market_state(_candles(prices), current_vix=14.0, trailing_vix_history=_long_vix_history(), context=_context())
    assert state.event_context.status == EVENT_CONTEXT_NOT_AVAILABLE


def test_confidence_is_never_fabricated_always_low_or_none_with_zero_samples():
    prices = [100.0 + i * 0.9 for i in range(30)]
    state = compose_market_state(_candles(prices), current_vix=14.0, trailing_vix_history=_long_vix_history(), context=_context())
    assert state.confidence.level in (CONFIDENCE_NONE, CONFIDENCE_LOW)
    assert state.confidence.sample_size == 0
    assert "20.1B" in state.confidence.method


def test_as_of_time_matches_context_never_wall_clock():
    prices = [100.0 + i * 0.9 for i in range(30)]
    as_of = datetime(2020, 3, 15, 9, 15)
    state = compose_market_state(
        _candles(prices), current_vix=14.0, trailing_vix_history=_long_vix_history(),
        context=_context(as_of=as_of),
    )
    assert state.as_of_time == as_of.isoformat()


def test_regime_brains_internal_confidence_never_surfaces_as_mic_confidence():
    """The heuristic 0.5-1.0 confidence RegimeBrain computes internally
    must never be re-exported as MIC v0's own confidence.level -- it is
    recorded in evidence, clearly labeled MODELED, but the top-level
    confidence field is independently governed by the zero-sample rule."""
    prices = [100.0 + i * 0.9 for i in range(30)]  # strongly trending -> RegimeBrain confidence near 1.0
    state = compose_market_state(_candles(prices), current_vix=14.0, trailing_vix_history=_long_vix_history(), context=_context())
    assert state.confidence.level != "HIGH"
    assert any("MODELED_confidence" in e for e in state.evidence)
