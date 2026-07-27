"""Production Engineering Sprint 3 -- Stage Interface Extraction.

Unit tests for market_observation.classify_candle(), the newly extracted
Market Observation classification. Before this extraction, duplicate/
stale/gap classification could only be exercised by feeding candles
through a full Orchestrator.on_candle() call. Now it is a pure function
of two timestamps and a candle interval, testable with plain values.
"""
from datetime import datetime, timedelta, timezone

from bujji.core.market_observation import (
    DUPLICATE_OR_STALE, GAP_DETECTED, OK, classify_candle,
)


def _ts(minute):
    return datetime(2026, 7, 13, 9, minute, tzinfo=timezone.utc)


def test_first_candle_ever_is_always_ok():
    result = classify_candle(_ts(20), None, candle_minutes=5)
    assert result.action == OK
    assert result.gap_seconds is None


def test_normal_next_candle_is_ok():
    result = classify_candle(_ts(25), _ts(20), candle_minutes=5)
    assert result.action == OK


def test_duplicate_timestamp_is_flagged():
    result = classify_candle(_ts(20), _ts(20), candle_minutes=5)
    assert result.action == DUPLICATE_OR_STALE


def test_stale_earlier_timestamp_is_flagged():
    result = classify_candle(_ts(15), _ts(20), candle_minutes=5)
    assert result.action == DUPLICATE_OR_STALE


def test_minor_jitter_within_1_5x_tolerance_is_still_ok():
    # 5-minute candles, actual gap 7 minutes (1.4x) -- within tolerance.
    result = classify_candle(_ts(27), _ts(20), candle_minutes=5)
    assert result.action == OK


def test_gap_beyond_1_5x_tolerance_is_detected():
    # 5-minute candles, actual gap 8 minutes (1.6x) -- exceeds 1.5x tolerance.
    result = classify_candle(_ts(28), _ts(20), candle_minutes=5)
    assert result.action == GAP_DETECTED
    assert result.gap_seconds == 480.0


def test_gap_boundary_exactly_at_1_5x_is_not_flagged():
    # Exactly 1.5x (7.5 minutes) -- the original code used a strict ">"
    # comparison, so exactly at the boundary must NOT be flagged.
    result = classify_candle(_ts(20) + timedelta(minutes=7, seconds=30), _ts(20), candle_minutes=5)
    assert result.action == OK


def test_pure_function_no_orchestrator_or_logging_dependency():
    """The whole point of the extraction: classify_candle takes no
    RuntimeStatus, no logger, no Orchestrator -- just two timestamps and
    an int. This test locks in that the module has no such import."""
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parent.parent / "bujji/core/market_observation.py").read_text())
    imported_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any("orchestrator" in m or "logging" in m or "runtime_status" in m for m in imported_modules)


def test_result_is_immutable():
    import pytest
    result = classify_candle(_ts(20), None, candle_minutes=5)
    with pytest.raises(Exception):
        result.action = "changed"
