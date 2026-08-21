"""Phase 20.1B -- mic_v0_validation tests. Synthetic fixtures only."""
from __future__ import annotations

from bujji.historical_reality.capture import build_historical_observation
from bujji.market_observation.taxonomy import RESOLUTION_FIVE_MINUTE
from bujji.mic_v0.models import REGIME_RANGE, REGIME_TREND, REGIME_UNCLEAR
from bujji.mic_v0_validation.models import DayClassification
from bujji.mic_v0_validation.validation import (
    _persistence,
    _reversal_frequency,
    build_validation_report,
    classify_day,
)

DAY = "2026-01-05"
INSTRUMENT = "NIFTY_FUT_CONTINUOUS"


def _obs(hms, open_, high, low, close):
    return build_historical_observation(
        instrument_identity=INSTRUMENT, instrument_type="FUTURE", resolution=RESOLUTION_FIVE_MINUTE,
        timestamp=f"{DAY}T{hms}+05:30", payload={"open": open_, "high": high, "low": low, "close": close},
        source="test", access_method="test", source_epoch=0, source_symbol=INSTRUMENT,
        raw_artifact_ref="test", ingestion_run_id="test", retrieved_at=f"{DAY}T16:00:00+05:30",
        certification_status="test",
    )


# --------------------------------------------------------------------- #
# Reversal frequency / persistence -- known sequences
# --------------------------------------------------------------------- #
def test_reversal_frequency_is_one_for_strict_zigzag():
    closes = [100, 101, 100, 101, 100, 101]  # every step reverses
    assert _reversal_frequency(closes) == 1.0


def test_reversal_frequency_is_zero_for_a_straight_line():
    closes = [100, 101, 102, 103, 104, 105]  # never reverses
    assert _reversal_frequency(closes) == 0.0


def test_persistence_is_high_for_a_straight_line():
    closes = [100, 101, 102, 103, 104, 105]
    assert _persistence(closes) == 5.0  # one run of length 5


def test_persistence_is_low_for_a_strict_zigzag():
    closes = [100, 101, 100, 101, 100, 101]
    assert _persistence(closes) == 1.0  # every step is its own run


def test_reversal_and_persistence_none_on_too_few_points():
    assert _reversal_frequency([100, 101]) is None
    assert _persistence([100, 101]) is None


# --------------------------------------------------------------------- #
# classify_day
# --------------------------------------------------------------------- #
def test_classify_day_returns_none_for_too_few_candles():
    rows = [_obs("09:15:00", 100, 101, 99, 100)]
    result = classify_day(DAY, rows, current_vix=14.0, trailing_vix=[14.0] * 100)
    assert result is None


def test_classify_day_on_a_real_shaped_trending_day():
    rows = []
    price = 100.0
    for i in range(30):
        price += 0.9
        rows.append(_obs(f"09:{15 + i:02d}:00", price, price + 0.3, price - 0.3, price))
    result = classify_day(DAY, rows, current_vix=14.0, trailing_vix=[14.0] * 100)
    assert result is not None
    assert result.market_regime == REGIME_TREND
    assert result.adx is not None
    assert result.efficiency_ratio is not None


# --------------------------------------------------------------------- #
# build_validation_report
# --------------------------------------------------------------------- #
def _dc(date, regime, adx, er, rev, pers):
    return DayClassification(
        date=date, market_regime=regime, adx=adx, efficiency_ratio=er,
        reversal_frequency=rev, persistence=pers, candles_used=30,
    )


def test_report_shows_separation_when_trend_metrics_genuinely_differ():
    classifications = (
        [_dc(f"2026-01-{i:02d}", REGIME_TREND, adx=40.0, er=0.7, rev=0.2, pers=4.0) for i in range(1, 25)]
        + [_dc(f"2026-02-{i:02d}", REGIME_RANGE, adx=12.0, er=0.15, rev=0.8, pers=1.2) for i in range(1, 25)]
    )
    report = build_validation_report(classifications)
    assert report.trend_stats.n == 24
    assert report.range_stats.n == 24
    assert "meaningful, consistent separation" in report.separation_conclusion


def test_report_flags_no_separation_when_metrics_do_not_differ():
    classifications = (
        [_dc(f"2026-01-{i:02d}", REGIME_TREND, adx=20.0, er=0.4, rev=0.5, pers=2.0) for i in range(1, 25)]
        + [_dc(f"2026-02-{i:02d}", REGIME_RANGE, adx=20.0, er=0.4, rev=0.5, pers=2.0) for i in range(1, 25)]
    )
    report = build_validation_report(classifications)
    assert "do NOT show meaningful separation" in report.separation_conclusion


def test_report_marks_small_samples_as_preliminary():
    classifications = (
        [_dc(f"2026-01-{i:02d}", REGIME_TREND, adx=40.0, er=0.7, rev=0.2, pers=4.0) for i in range(1, 6)]
        + [_dc(f"2026-02-{i:02d}", REGIME_RANGE, adx=12.0, er=0.15, rev=0.8, pers=1.2) for i in range(1, 6)]
    )
    report = build_validation_report(classifications)
    assert "PRELIMINARY" in report.separation_conclusion


def test_report_never_uses_strategy_pnl_only_market_metrics():
    """Structural proof: DayClassification has no P&L field at all."""
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(DayClassification)}
    for forbidden in ("pnl", "profit", "return_pct", "trade_result"):
        assert forbidden not in field_names


def test_report_handles_empty_classifications():
    report = build_validation_report([])
    assert report.total_days == 0
    assert "INCONCLUSIVE" in report.separation_conclusion
