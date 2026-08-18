"""Tests -- Phase 20.27 Microstructure Intelligence Layer.

No broker, no live tick feed, no network -- every `MinuteObservation`
here is a plain, caller-constructed fixture."""
from __future__ import annotations

import dataclasses
from pathlib import Path

from bujji.market_microstructure.models import KIND_SPOT, MinuteObservation
from bujji.microstructure_intelligence import (
    DataQuality, MicrostructureClassifier, MicrostructureReading,
    MicrostructureState, explain_microstructure_reading,
)

INSTRUMENT = "NSE:NIFTY50-INDEX"


def _ts(minute_offset: int) -> str:
    hour = 9 + (15 + minute_offset) // 60
    minute = (15 + minute_offset) % 60
    return f"2026-08-17T{hour:02d}:{minute:02d}:00+05:30"


def _obs(
    idx: int, *, open_: float, high: float, low: float, close: float,
    tick_count: int, max_silence: float = 5.0, session_date: str = "2026-08-17",
) -> MinuteObservation:
    return MinuteObservation(
        instrument=INSTRUMENT, kind=KIND_SPOT, session_date=session_date,
        window_start=_ts(idx), window_end=_ts(idx + 1),
        open=open_, high=high, low=low, close=close,
        tick_count=tick_count, max_tick_silence_seconds=max_silence,
        avg_tick_interval_seconds=60.0 / tick_count if tick_count else None,
        max_price_move=abs(high - low),
        first_tick_timestamp=_ts(idx), last_tick_timestamp=_ts(idx + 1),
    )


def _flat_series(n: int, *, tick_count: int, close: float = 24500.0, range_pts: float = 15.0, silence: float = 5.0):
    return [
        _obs(i, open_=close, high=close + range_pts / 2, low=close - range_pts / 2,
             close=close, tick_count=tick_count, max_silence=silence)
        for i in range(n)
    ]


# --------------------------------------------------------------------- #
# 1. Insufficient data
# --------------------------------------------------------------------- #

def test_insufficient_data_below_minimum_returns_unknown():
    obs = _flat_series(3, tick_count=5)
    reading = MicrostructureClassifier().analyze(obs)
    assert reading.state == MicrostructureState.UNKNOWN
    assert reading.data_quality == DataQuality.INSUFFICIENT
    assert "insufficient_data" in reading.reasons[0]
    assert reading.confidence == 0.0


def test_empty_observations_returns_unknown_without_crashing():
    reading = MicrostructureClassifier().analyze([])
    assert reading.state == MicrostructureState.UNKNOWN
    assert reading.data_quality == DataQuality.INSUFFICIENT
    assert reading.instrument is None
    assert reading.window_start is None and reading.window_end is None


def test_mixed_instruments_fails_closed():
    obs = _flat_series(6, tick_count=5)
    mixed = list(obs)
    mixed[3] = dataclasses.replace(mixed[3], instrument="NSE:BANKNIFTY-INDEX")
    reading = MicrostructureClassifier().analyze(mixed)
    assert reading.state == MicrostructureState.UNKNOWN
    assert reading.data_quality == DataQuality.INSUFFICIENT
    assert "mixed_instruments" in reading.reasons[0]


# --------------------------------------------------------------------- #
# 2. Quiet market
# --------------------------------------------------------------------- #

def test_quiet_market_low_density_and_small_range():
    obs = _flat_series(6, tick_count=2, close=24500.0, range_pts=1.0, silence=5.0)
    reading = MicrostructureClassifier().analyze(obs)
    assert reading.state == MicrostructureState.QUIET
    assert reading.data_quality == DataQuality.SUFFICIENT


# --------------------------------------------------------------------- #
# 3. Normal market
# --------------------------------------------------------------------- #

def test_normal_market_moderate_steady_activity():
    closes = [24500.0, 24505.0, 24498.0, 24503.0, 24497.0, 24502.0]
    obs = [
        _obs(i, open_=closes[i] - 5, high=closes[i] + 7.5, low=closes[i] - 7.5,
             close=closes[i], tick_count=5, max_silence=8.0)
        for i in range(6)
    ]
    reading = MicrostructureClassifier().analyze(obs)
    assert reading.state == MicrostructureState.NORMAL
    assert reading.data_quality == DataQuality.SUFFICIENT
    assert reading.confidence == 0.5


# --------------------------------------------------------------------- #
# 4. Expansion
# --------------------------------------------------------------------- #

def test_expansion_rising_density_range_and_direction():
    first = [
        _obs(0, open_=24499, high=24502, low=24497, close=24500, tick_count=3),
        _obs(1, open_=24500, high=24503, low=24498, close=24500, tick_count=3),
        _obs(2, open_=24500, high=24503, low=24498, close=24500, tick_count=3),
    ]
    second = [
        _obs(3, open_=24500, high=24512, low=24499, close=24510, tick_count=6),
        _obs(4, open_=24510, high=24522, low=24509, close=24520, tick_count=6),
        _obs(5, open_=24520, high=24532, low=24519, close=24530, tick_count=6),
    ]
    reading = MicrostructureClassifier().analyze(first + second)
    assert reading.state == MicrostructureState.EXPANSION
    assert reading.supporting_metrics["density_ratio"] >= 1.3
    assert reading.supporting_metrics["range_ratio"] >= 1.3


# --------------------------------------------------------------------- #
# 5. Contraction
# --------------------------------------------------------------------- #

def test_contraction_shrinking_range_and_activity():
    first = _flat_series(3, tick_count=8, close=24500.0, range_pts=20.0)
    second = _flat_series(3, tick_count=4, close=24500.0, range_pts=8.0)
    second = [dataclasses.replace(o, window_start=_ts(i + 3), window_end=_ts(i + 4)) for i, o in enumerate(second)]
    reading = MicrostructureClassifier().analyze(first + second)
    assert reading.state == MicrostructureState.CONTRACTION
    assert reading.supporting_metrics["range_ratio"] <= 0.7
    assert reading.supporting_metrics["density_ratio"] <= 0.85


# --------------------------------------------------------------------- #
# 6. Absorption
# --------------------------------------------------------------------- #

def test_absorption_high_activity_limited_movement():
    first = _flat_series(3, tick_count=4, close=24500.0, range_pts=15.0)
    second = _flat_series(3, tick_count=8, close=24500.0, range_pts=10.0)
    second = [dataclasses.replace(o, window_start=_ts(i + 3), window_end=_ts(i + 4)) for i, o in enumerate(second)]
    reading = MicrostructureClassifier().analyze(first + second)
    assert reading.state == MicrostructureState.ABSORPTION
    assert reading.supporting_metrics["density_ratio"] >= 1.3
    assert reading.supporting_metrics["range_ratio"] <= 0.9


# --------------------------------------------------------------------- #
# 7. Liquidity stress
# --------------------------------------------------------------------- #

def test_liquidity_stress_abnormal_silence_gap():
    obs = _flat_series(6, tick_count=5, silence=10.0)
    obs[-1] = dataclasses.replace(obs[-1], max_tick_silence_seconds=45.0)
    reading = MicrostructureClassifier().analyze(obs)
    assert reading.state == MicrostructureState.LIQUIDITY_STRESS
    assert "max_tick_silence_seconds" in reading.reasons[0]


def test_liquidity_stress_irregular_tick_cadence():
    obs = _flat_series(6, tick_count=5, silence=5.0)
    bursty_counts = [1, 1, 1, 20, 1, 1]
    obs = [dataclasses.replace(o, tick_count=bursty_counts[i]) for i, o in enumerate(obs)]
    reading = MicrostructureClassifier().analyze(obs)
    assert reading.state == MicrostructureState.LIQUIDITY_STRESS
    assert any("tick_count_cv" in r for r in reading.reasons)


# --------------------------------------------------------------------- #
# 8. False breakout
# --------------------------------------------------------------------- #

def test_false_breakout_expansion_then_immediate_rejection():
    first = [
        _obs(0, open_=24499, high=24502, low=24498, close=24500, tick_count=5),
        _obs(1, open_=24500, high=24522, low=24499, close=24520, tick_count=5),
        _obs(2, open_=24520, high=24542, low=24519, close=24540, tick_count=5),
    ]
    second = [
        _obs(3, open_=24540, high=24541, low=24538, close=24540, tick_count=5),
        _obs(4, open_=24540, high=24541, low=24518, close=24520, tick_count=5),
        _obs(5, open_=24520, high=24521, low=24498, close=24500, tick_count=5),
    ]
    reading = MicrostructureClassifier().analyze(first + second)
    assert reading.state == MicrostructureState.FALSE_BREAKOUT
    assert reading.supporting_metrics["first_half_net_move"] > 0
    assert reading.supporting_metrics["second_half_net_move"] < 0


# --------------------------------------------------------------------- #
# 9. Deterministic output
# --------------------------------------------------------------------- #

def test_deterministic_output_same_input_same_reading():
    first = [
        _obs(0, open_=24499, high=24502, low=24497, close=24500, tick_count=3),
        _obs(1, open_=24500, high=24503, low=24498, close=24500, tick_count=3),
        _obs(2, open_=24500, high=24503, low=24498, close=24500, tick_count=3),
    ]
    second = [
        _obs(3, open_=24500, high=24512, low=24499, close=24510, tick_count=6),
        _obs(4, open_=24510, high=24522, low=24509, close=24520, tick_count=6),
        _obs(5, open_=24520, high=24532, low=24519, close=24530, tick_count=6),
    ]
    obs = first + second
    reading_a = MicrostructureClassifier().analyze(obs)
    reading_b = MicrostructureClassifier().analyze(list(obs))  # fresh instance, fresh list object
    assert reading_a == reading_b


# --------------------------------------------------------------------- #
# 10. No future data leakage
# --------------------------------------------------------------------- #

def test_no_future_data_leakage():
    all_obs = _flat_series(9, tick_count=3, close=24500.0, range_pts=5.0)
    # Make the tail (index 6-8) wildly different from the head -- if the
    # classifier ever looked past the slice it was given, this would
    # change reading_short's result.
    all_obs[6:9] = [
        _obs(i, open_=24500 + i * 50, high=24500 + i * 50 + 100, low=24500 + i * 50 - 100,
             close=24500 + i * 60, tick_count=50, max_silence=90.0)
        for i in range(6, 9)
    ]

    classifier = MicrostructureClassifier()
    short_list = all_obs[:6]
    reading_short = classifier.analyze(short_list)

    long_list = all_obs[:9]
    _reading_long = classifier.analyze(long_list)  # exercised, deliberately not asserted on

    reading_short_recomputed = classifier.analyze(short_list)
    assert reading_short == reading_short_recomputed


# --------------------------------------------------------------------- #
# 11. Explainability output
# --------------------------------------------------------------------- #

def test_explainability_output_for_expansion():
    first = [
        _obs(0, open_=24499, high=24502, low=24497, close=24500, tick_count=3),
        _obs(1, open_=24500, high=24503, low=24498, close=24500, tick_count=3),
        _obs(2, open_=24500, high=24503, low=24498, close=24500, tick_count=3),
    ]
    second = [
        _obs(3, open_=24500, high=24512, low=24499, close=24510, tick_count=6),
        _obs(4, open_=24510, high=24522, low=24509, close=24520, tick_count=6),
        _obs(5, open_=24520, high=24532, low=24519, close=24530, tick_count=6),
    ]
    reading = MicrostructureClassifier().analyze(first + second)
    text = explain_microstructure_reading(reading)
    assert text.startswith("MICROSTRUCTURE: EXPANSION.")
    assert "Confidence:" in text
    assert "Data quality:" in text


def test_explainability_output_for_unknown_does_not_crash():
    reading = MicrostructureClassifier().analyze([])
    text = explain_microstructure_reading(reading)
    assert text.startswith("MICROSTRUCTURE: UNKNOWN.")
    assert "Confidence: 0%" in text


# --------------------------------------------------------------------- #
# No generic "score" field (explicit instruction)
# --------------------------------------------------------------------- #

def test_no_generic_score_field_on_model():
    field_names = {f.name for f in dataclasses.fields(MicrostructureReading)}
    assert "score" not in field_names


def test_no_generic_score_key_in_supporting_metrics():
    obs = _flat_series(6, tick_count=5)
    reading = MicrostructureClassifier().analyze(obs)
    assert "score" not in reading.supporting_metrics


# --------------------------------------------------------------------- #
# Safety boundary: no broker/execution imports anywhere in this package
# --------------------------------------------------------------------- #

def test_no_broker_or_execution_imports_in_package():
    pkg_dir = Path(__file__).resolve().parent.parent / "bujji" / "microstructure_intelligence"
    forbidden = ("import bujji.broker", "from bujji.broker", "import bujji.execution", "from bujji.execution",
                 "place_order", "modify_order", "cancel_order")
    for py_file in pkg_dir.glob("*.py"):
        source = py_file.read_text()
        for pattern in forbidden:
            assert pattern not in source, f"{pattern!r} found in {py_file.name}"
