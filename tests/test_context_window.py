"""Tests -- Market Context Window, Phase 11 Upgrade 3. Pure functions
over plain dict lists, no broker, no live calls, no prediction."""
from __future__ import annotations

import json

from bujji.context_window.engine import build_context_window


def _record(direction=None, volatility_regime=None, regime=None, structure_location=None):
    return {
        "market_direction": {"overall_direction": direction} if direction else None,
        "volatility_structure": {"volatility_regime": volatility_regime} if volatility_regime else None,
        "market_state": {"regime": regime} if regime else None,
        "market_structure": {"structure_location": structure_location} if structure_location else None,
    }


def test_empty_records_never_raises_and_is_all_unknown():
    report = build_context_window([])
    assert report.short_term.cycles_observed == 0
    assert report.short_term.direction is None
    assert report.session_context.range_status == "UNKNOWN"


def test_historical_context_always_honestly_unavailable():
    """No cross-session index exists -- must never fabricate a
    similar-sessions count."""
    records = [_record(direction="BULLISH") for _ in range(5)]
    report = build_context_window(records)
    assert report.historical_context.available is False
    assert report.historical_context.similar_sessions_found == 0


def test_latest_direction_picked_from_most_recent_resolved_reading():
    records = [_record(direction="BULLISH"), _record(direction=None), _record(direction="BEARISH")]
    report = build_context_window(records)
    assert report.short_term.direction == "BEARISH"


def test_volatility_trend_rising_on_real_regime_change():
    records = [_record(volatility_regime="COMPRESSED")] * 5 + [_record(volatility_regime="HIGH_VOLATILITY")] * 5
    report = build_context_window(records)
    assert report.short_term.volatility_trend == "RISING"


def test_volatility_trend_falling_on_real_regime_change():
    records = [_record(volatility_regime="HIGH_VOLATILITY")] * 5 + [_record(volatility_regime="COMPRESSED")] * 5
    report = build_context_window(records)
    assert report.short_term.volatility_trend == "FALLING"


def test_volatility_trend_unknown_with_insufficient_readings():
    records = [_record()] * 5
    report = build_context_window(records)
    assert report.short_term.volatility_trend == "UNKNOWN"


def test_dominant_regime_is_most_frequent_not_most_recent():
    records = [_record(regime="RANGING")] * 8 + [_record(regime="TRANSITIONING")] * 2
    report = build_context_window(records)
    assert report.session_context.dominant_regime == "RANGING"


def test_range_status_established_when_majority_inside_range():
    records = [_record(structure_location="INSIDE_RANGE")] * 7 + [_record(structure_location="NEAR_RESISTANCE")] * 3
    report = build_context_window(records)
    assert report.session_context.range_status == "ESTABLISHED"


def test_range_status_not_established_when_minority_inside_range():
    records = [_record(structure_location="NEAR_RESISTANCE")] * 7 + [_record(structure_location="INSIDE_RANGE")] * 3
    report = build_context_window(records)
    assert report.session_context.range_status == "NOT_ESTABLISHED"


def test_short_term_window_smaller_than_medium_term():
    records = [_record(regime="RANGING") for _ in range(500)]
    report = build_context_window(records, short_term_cycles=20, medium_term_cycles=240)
    assert report.short_term.cycles_observed == 20
    assert report.medium_term.cycles_observed == 240
    assert report.session_context.cycles_observed == 500


def test_report_json_serializable():
    records = [_record(direction="BULLISH", volatility_regime="COMPRESSED", regime="RANGING", structure_location="INSIDE_RANGE")]
    report = build_context_window(records)
    json.dumps(report.to_dict())
