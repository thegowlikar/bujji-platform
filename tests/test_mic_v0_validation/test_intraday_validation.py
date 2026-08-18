"""Phase 20.1C -- intraday_validation tests. Synthetic fixtures only."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from bujji.core.models import Candle
from bujji.mic_v0.models import MarketState, ConfidenceInfo, CONFIDENCE_LOW, RISK_NORMAL, VOLATILITY_NORMAL, REGIME_RANGE, ENV_MEAN_REVERSION
from bujji.mic_v0_validation.intraday_validation import (
    build_intraday_validation_report,
    classify_intraday_window,
    combine_session_and_intraday,
    generate_rolling_windows,
)
from bujji.mic_v0_validation.models_intraday import (
    INTRADAY_RANGE,
    INTRADAY_TREND_DOWN,
    INTRADAY_TREND_UP,
    IntradayWindowReading,
)

BASE = datetime(2026, 1, 5, 9, 15)
DAY = "2026-01-05"


def _candles(prices, base=BASE, step_minutes=5):
    return [
        Candle(timestamp=base + timedelta(minutes=step_minutes * i), open=p, high=p + 0.2, low=p - 0.2, close=p)
        for i, p in enumerate(prices)
    ]


# --------------------------------------------------------------------- #
# generate_rolling_windows
# --------------------------------------------------------------------- #
def test_generate_rolling_windows_empty_input():
    assert generate_rolling_windows([], 30) == []


def test_generate_rolling_windows_produces_expected_count():
    # 09:15 to 10:15, 5-min candles = 13 candles (09:15..10:15 inclusive... actually 13 points).
    candles = _candles([100.0] * 13)  # spans 09:15 to 10:15
    windows = generate_rolling_windows(candles, window_minutes=30, step_minutes=5)
    assert len(windows) > 0
    for w in windows:
        span = (w[-1].timestamp - w[0].timestamp).total_seconds() / 60
        assert span <= 30


def test_generate_rolling_windows_never_fabricates_candles():
    candles = _candles([100.0] * 13)
    windows = generate_rolling_windows(candles, window_minutes=30)
    all_used = [c for w in windows for c in w]
    real_set = set(candles)
    assert set(all_used).issubset(real_set)


# --------------------------------------------------------------------- #
# classify_intraday_window
# --------------------------------------------------------------------- #
def test_classify_returns_none_for_too_few_candles():
    candles = _candles([100.0])
    assert classify_intraday_window(DAY, candles, 30) is None


def test_uptrend_window_classified_trend_up():
    prices = [100.0 + i * 0.9 for i in range(8)]
    reading = classify_intraday_window(DAY, _candles(prices), 30)
    assert reading is not None
    assert reading.intraday_regime == INTRADAY_TREND_UP


def test_downtrend_window_classified_trend_down():
    prices = [100.0 - i * 0.9 for i in range(8)]
    reading = classify_intraday_window(DAY, _candles(prices), 30)
    assert reading is not None
    assert reading.intraday_regime == INTRADAY_TREND_DOWN


def test_range_window_classified_range():
    prices = [100.0 + 0.3 * math.sin(i * (2 * math.pi / 8)) for i in range(16)]
    reading = classify_intraday_window(DAY, _candles(prices), 60)
    assert reading is not None
    assert reading.intraday_regime == INTRADAY_RANGE


def test_breakout_attempt_and_no_trade_are_never_emitted():
    """Disclosed limitation: these two vocabulary states are listed in
    models_intraday but have no real detection logic behind them yet
    -- classify_intraday_window must never emit them, matching the
    module's own honesty disclosure rather than silently forcing a
    classification into an unimplemented bucket."""
    from bujji.mic_v0_validation.models_intraday import INTRADAY_BREAKOUT_ATTEMPT, INTRADAY_NO_TRADE

    fixtures = [
        [100.0 + i * 0.9 for i in range(8)],                                   # trend up
        [100.0 - i * 0.9 for i in range(8)],                                   # trend down
        [100.0 + 0.3 * math.sin(i * (2 * math.pi / 8)) for i in range(16)],    # range
        [100.0] * 8,                                                            # flat
    ]
    for prices in fixtures:
        reading = classify_intraday_window(DAY, _candles(prices), 30)
        if reading is not None:
            assert reading.intraday_regime not in (INTRADAY_BREAKOUT_ATTEMPT, INTRADAY_NO_TRADE)


def test_reading_records_real_window_label_and_candle_count():
    prices = [100.0 + i * 0.9 for i in range(8)]
    candles = _candles(prices)
    reading = classify_intraday_window(DAY, candles, 30)
    assert reading.candles_used == 8
    assert reading.window_label == f"{candles[0].timestamp.strftime('%H:%M')}-{candles[-1].timestamp.strftime('%H:%M')}"


# --------------------------------------------------------------------- #
# build_intraday_validation_report
# --------------------------------------------------------------------- #
def _reading(regime, wm, er, adx, rev, pers):
    return IntradayWindowReading(
        date=DAY, window_label="x", window_minutes=wm, intraday_regime=regime,
        efficiency_ratio=er, adx=adx, realized_vol=0.001, compression_ratio=1.0,
        reversal_frequency=rev, persistence=pers, candles_used=6,
    )


def test_report_flags_insufficient_samples_below_minimum():
    readings = [_reading(INTRADAY_TREND_UP, 30, 0.7, 40, 0.2, 4.0) for _ in range(5)] + \
               [_reading(INTRADAY_RANGE, 30, 0.1, 12, 0.8, 1.2) for _ in range(5)]
    report = build_intraday_validation_report(readings, total_days=1)
    wr30 = next(w for w in report.window_reports if w.window_minutes == 30)
    assert wr30.sufficient_sample_size is False
    assert "INSUFFICIENT SAMPLES" in wr30.separation_conclusion


def test_report_shows_separation_with_enough_distinct_samples():
    readings = [_reading(INTRADAY_TREND_UP, 30, 0.7, 40, 0.2, 4.0) for _ in range(40)] + \
               [_reading(INTRADAY_RANGE, 30, 0.1, 12, 0.8, 1.2) for _ in range(40)]
    report = build_intraday_validation_report(readings, total_days=5)
    wr30 = next(w for w in report.window_reports if w.window_minutes == 30)
    assert wr30.sufficient_sample_size is True
    assert "Meaningful separation" in wr30.separation_conclusion
    assert report.overall_pass is True


def test_report_combines_trend_up_and_trend_down_for_comparison():
    readings = (
        [_reading(INTRADAY_TREND_UP, 30, 0.7, 40, 0.2, 4.0) for _ in range(20)]
        + [_reading(INTRADAY_TREND_DOWN, 30, 0.7, 40, 0.2, 4.0) for _ in range(20)]
        + [_reading(INTRADAY_RANGE, 30, 0.1, 12, 0.8, 1.2) for _ in range(40)]
    )
    report = build_intraday_validation_report(readings, total_days=5)
    wr30 = next(w for w in report.window_reports if w.window_minutes == 30)
    assert wr30.sufficient_sample_size is True
    trend_up_stats = next(gs for gs in wr30.group_stats if gs.label == INTRADAY_TREND_UP)
    trend_down_stats = next(gs for gs in wr30.group_stats if gs.label == INTRADAY_TREND_DOWN)
    assert trend_up_stats.n == 20
    assert trend_down_stats.n == 20


def test_overall_fail_when_no_window_length_shows_separation():
    readings = [_reading(INTRADAY_TREND_UP, 30, 0.2, 15, 0.5, 2.0) for _ in range(40)] + \
               [_reading(INTRADAY_RANGE, 30, 0.2, 15, 0.5, 2.0) for _ in range(40)]
    report = build_intraday_validation_report(readings, total_days=5)
    assert report.overall_pass is False
    assert report.overall_conclusion == "MIC intraday validation inconclusive."


# --------------------------------------------------------------------- #
# combine_session_and_intraday
# --------------------------------------------------------------------- #
def test_combine_matches_charter_shape():
    session_state = MarketState(
        as_of_time="2026-01-05T15:30:00+05:30", market_regime=REGIME_RANGE,
        volatility_state=VOLATILITY_NORMAL, risk_state=RISK_NORMAL,
        recommended_environment=ENV_MEAN_REVERSION, evidence=(),
        confidence=ConfidenceInfo(level=CONFIDENCE_LOW, sample_size=0, method="x"),
    )
    window_reading = _reading(INTRADAY_TREND_UP, 60, 0.64, 32, 0.2, 3.0)
    combined = combine_session_and_intraday(session_state, window_reading)
    assert combined["session_regime"] == REGIME_RANGE
    assert combined["intraday_regime"] == INTRADAY_TREND_UP
    assert combined["evidence"]["efficiency_ratio"] == 0.64
    assert combined["evidence"]["adx"] == 32
    assert combined["confidence"] == {"level": "LOW", "sample_size": 0}
