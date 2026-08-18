"""Tests -- Intelligence Consistency Checker, Phase 12 Task 7. Pure
functions over plain dicts, report-only, never auto-corrects."""
from __future__ import annotations

from bujji.intelligence_consistency_checker.engine import check_cycle, check_session


def test_no_warnings_on_empty_record():
    assert check_cycle({}) == ()


def test_directional_strategy_in_ranging_regime_flagged():
    record = {
        "market_state": {"regime": "RANGING"},
        "strategy_selection": {"selected_strategy_family": "LONG_DIRECTIONAL", "confidence": "MODERATE"},
        "market_direction": {"overall_direction": "BULLISH"},
    }
    warnings = check_cycle(record)
    assert any("Regime/Strategy mismatch" in w for w in warnings)


def test_range_strategy_in_trending_regime_flagged():
    record = {
        "market_state": {"regime": "TRENDING"},
        "strategy_selection": {"selected_strategy_family": "IRON_FLY", "confidence": "MODERATE"},
    }
    warnings = check_cycle(record)
    assert any("range-bound" in w for w in warnings)


def test_no_warning_when_regime_and_strategy_align():
    record = {
        "market_state": {"regime": "RANGING"},
        "strategy_selection": {"selected_strategy_family": "IRON_FLY", "confidence": "MODERATE"},
        "market_direction": {"overall_direction": "NEUTRAL"},
    }
    warnings = check_cycle(record)
    assert warnings == ()


def test_directional_strategy_with_unknown_direction_flagged():
    record = {
        "strategy_selection": {"selected_strategy_family": "SHORT_DIRECTIONAL", "confidence": "MODERATE"},
        "market_direction": {"overall_direction": "UNKNOWN"},
    }
    warnings = check_cycle(record)
    assert any("Direction/Strategy mismatch" in w for w in warnings)


def test_high_confidence_despite_no_consensus_flagged():
    record = {
        "strategy_selection": {"selected_strategy_family": "IRON_FLY", "confidence": "HIGH"},
        "consensus": {"consensus_level": "NO_CONSENSUS"},
    }
    warnings = check_cycle(record)
    assert any("Consensus/Confidence mismatch" in w for w in warnings)


def test_no_strategy_selected_never_flagged():
    record = {"market_state": {"regime": "RANGING"}, "strategy_selection": {"selected_strategy_family": None}}
    assert check_cycle(record) == ()


def test_never_auto_corrects_only_reports():
    """This module must never mutate the input record."""
    record = {
        "market_state": {"regime": "RANGING"},
        "strategy_selection": {"selected_strategy_family": "LONG_DIRECTIONAL", "confidence": "MODERATE"},
    }
    original = dict(record)
    check_cycle(record)
    assert record["strategy_selection"]["selected_strategy_family"] == "LONG_DIRECTIONAL"


def test_check_session_is_pure_map():
    records = [
        {"market_state": {"regime": "RANGING"}, "strategy_selection": {"selected_strategy_family": "LONG_DIRECTIONAL", "confidence": "MODERATE"}},
        {},
    ]
    results = check_session(records)
    assert len(results) == 2
    assert results[0] != ()
    assert results[1] == ()
