"""Tests -- Shadow Validation (Recorder, Health Metrics, Strategy
Reasoning, Session Summary, Memory Validation), Phase 12 Tasks 2-6.
Pure functions over plain dicts, no broker, no live calls."""
from __future__ import annotations

import json

from bujji.shadow_validation.engine import build_validation_record, build_validation_session
from bujji.shadow_validation.health_metrics import honesty_metrics, understanding_quality
from bujji.shadow_validation.memory_validation import validate_market_memory
from bujji.shadow_validation.session_summary import build_session_intelligence_summary
from bujji.shadow_validation.strategy_reasoning import build_strategy_reasoning_report, render_strategy_reasoning_text


# ---------------------------------------------------------------------------
# Task 2 -- Shadow Validation Recorder
# ---------------------------------------------------------------------------
def test_validation_record_matches_requested_schema():
    record = {
        "timestamp": "2026-08-06T09:15:00+05:30",
        "market_state": {"regime": "RANGING"},
        "market_direction": {"overall_direction": "NEUTRAL"},
        "consensus": {"consensus_level": "MODERATE_CONSENSUS"},
        "opportunity": {"opportunity_state": "MONITOR"},
        "trade_thesis": {"thesis_type": "NO_TRADE"},
        "strategy_selection": {"selected_strategy_family": "IRON_FLY", "confidence": "MODERATE"},
        "trade_intent": {"intent_state": "INTENT_STATE_FORMED"},
    }
    row = build_validation_record(record)
    assert set(row.keys()) == {
        "time", "market_regime", "market_direction", "consensus", "opportunity",
        "trade_thesis", "selected_strategy", "selection_confidence", "trade_intent",
        "completeness_score", "unknown_domains", "contradictions",
    }
    assert row["market_regime"] == "RANGING"
    assert row["selected_strategy"] == "IRON_FLY"


def test_validation_record_never_raises_on_empty_input():
    row = build_validation_record({})
    assert row["market_regime"] is None
    assert row["unknown_domains"] == []
    assert row["contradictions"] == []


def test_validation_record_pulls_unknown_domains_from_completeness():
    completeness = {"domains": {"price_structure": {"status": "COMPLETE"}, "liquidity": {"status": "UNKNOWN"}}}
    row = build_validation_record({}, completeness=completeness)
    assert row["unknown_domains"] == ["liquidity"]


def test_validation_record_merges_narrative_and_checker_contradictions():
    record = {"narrative": {"contradictions": ["price direction without participant confirmation"]}}
    row = build_validation_record(record, consistency_warnings=["Regime/Strategy mismatch: X"])
    assert "price direction without participant confirmation" in row["contradictions"]
    assert "Regime/Strategy mismatch: X" in row["contradictions"]


def test_validation_session_is_pure_map():
    records = [{"timestamp": f"t{i}"} for i in range(3)]
    rows = build_validation_session(records)
    assert len(rows) == 3
    assert [r["time"] for r in rows] == ["t0", "t1", "t2"]


def test_validation_record_json_serializable():
    row = build_validation_record({"timestamp": "t"})
    json.dumps(row)


# ---------------------------------------------------------------------------
# Task 3 -- Intelligence Health Metrics
# ---------------------------------------------------------------------------
def test_understanding_quality_percentages_sum_to_100():
    reports = [
        {"domains": {"a": {"status": "COMPLETE"}, "b": {"status": "PARTIAL"}, "c": {"status": "UNKNOWN"}}},
    ]
    q = understanding_quality(reports)
    assert abs((q["complete_pct"] + q["partial_pct"] + q["unknown_pct"]) - 100.0) < 0.1
    assert q["domain_cycles_observed"] == 3


def test_understanding_quality_none_with_no_data():
    q = understanding_quality([])
    assert q["complete_pct"] is None
    assert q["domain_cycles_observed"] == 0


def test_honesty_metrics_prefers_unknown_over_false_confidence():
    """The core Phase 12 philosophy: UNKNOWN correctly reported must be
    tallied as a POSITIVE count, never conflated with false confidence."""
    reports = [
        {"domains": {"a": {"status": "UNKNOWN"}}, "honesty_score": 100.0},
        {"domains": {"a": {"status": "UNKNOWN"}}, "honesty_score": 100.0},
    ]
    m = honesty_metrics(reports)
    assert m["unknown_correctly_reported"] == 2
    assert m["false_confidence_events"] == 0


def test_honesty_metrics_flags_low_honesty_score_as_false_confidence():
    reports = [{"domains": {"a": {"status": "COMPLETE"}}, "honesty_score": 66.7}]
    m = honesty_metrics(reports)
    assert m["false_confidence_events"] == 1


def test_honesty_metrics_counts_contradiction_events_from_validation_rows():
    validation_rows = [{"contradictions": ["x"]}, {"contradictions": []}]
    m = honesty_metrics([], validation_rows)
    assert m["contradiction_events"] == 1


# ---------------------------------------------------------------------------
# Task 4 -- Strategy Reasoning Validation
# ---------------------------------------------------------------------------
def test_strategy_reasoning_report_structure():
    record = {
        "market_state": {"regime": "RANGING"},
        "volatility_structure": {"volatility_regime": "COMPRESSED"},
        "liquidity": {"tightness": "TIGHT"},
        "consensus": {"consensus_level": "MODERATE_CONSENSUS"},
        "strategy_suitability": [
            {"strategy_family": "IRON_FLY", "suitability": "SUITABLE", "confidence": "MODERATE",
             "supporting_reasons": ["structure supports a bounded read"], "rejecting_reasons": []},
        ],
    }
    report = build_strategy_reasoning_report(record)
    assert report["market_context"]["regime"] == "RANGING"
    assert report["families"][0]["family"] == "IRON_FLY"
    assert report["families"][0]["reason"] == "structure supports a bounded read"


def test_strategy_reasoning_never_fabricates_reason():
    record = {"strategy_suitability": [{"strategy_family": "X", "suitability": "UNSUITABLE"}]}
    report = build_strategy_reasoning_report(record)
    assert report["families"][0]["reason"] == "no reasoning disclosed"


def test_strategy_reasoning_text_has_no_execution_language():
    record = {"strategy_suitability": [{"strategy_family": "IRON_FLY", "suitability": "SUITABLE", "supporting_reasons": ["ok"]}]}
    report = build_strategy_reasoning_report(record)
    text = render_strategy_reasoning_text(report).lower()
    for forbidden in ("place order", "buy now", "sell now", "execute"):
        assert forbidden not in text


# ---------------------------------------------------------------------------
# Task 5 -- Decision Timeline Memory
# ---------------------------------------------------------------------------
def test_market_evolution_only_records_real_changes():
    records = [
        {"timestamp": "t0", "market_state": {"regime": "RANGING"}},
        {"timestamp": "t1", "market_state": {"regime": "RANGING"}},
        {"timestamp": "t2", "market_state": {"regime": "TRENDING"}},
    ]
    summary = build_session_intelligence_summary(records)
    assert summary["market_evolution"] == [
        {"time": "t0", "regime": "RANGING"},
        {"time": "t2", "regime": "TRENDING"},
    ]


def test_strategy_evolution_tracks_family_changes():
    records = [
        {"timestamp": "t0", "strategy_selection": {"selected_strategy_family": None}},
        {"timestamp": "t1", "strategy_selection": {"selected_strategy_family": "VOLATILITY_EXPANSION"}},
    ]
    summary = build_session_intelligence_summary(records)
    assert len(summary["strategy_evolution"]) == 2
    assert "VOLATILITY_EXPANSION" in summary["strategy_evolution"][1]["event"]


def test_intelligence_maturity_samples_real_scores():
    records = [{"timestamp": f"t{i}"} for i in range(5)]
    scores = [10.0, 20.0, 30.0, 40.0, 90.0]
    summary = build_session_intelligence_summary(records, scores)
    assert summary["intelligence_maturity"]["opening"] == 10.0
    assert summary["intelligence_maturity"]["closing"] == 90.0


def test_session_summary_never_raises_on_empty_session():
    summary = build_session_intelligence_summary([])
    assert summary["cycles_observed"] == 0
    assert summary["market_evolution"] == []


# ---------------------------------------------------------------------------
# Task 6 -- Market Memory Validation
# ---------------------------------------------------------------------------
def test_memory_validation_detects_real_growth():
    records = [
        {"memory_health": {"historical_events_available": 10, "episode_count": 1, "unresolved_event_references": 0}},
        {"memory_health": {"historical_events_available": 50, "episode_count": 1, "unresolved_event_references": 0}},
        {"regime_memory": {"duration_cycles": 5, "total_transitions_observed": 2}},
    ]
    report = validate_market_memory(records)
    assert report["events_accumulate_correctly"] is True
    assert report["event_growth_rate"] > 0
    assert report["episodes_evolve_correctly"] is True
    assert report["structure_uses_history"] is True
    assert report["regime_duration_cycles"] == 5
    assert report["transition_count"] == 2


def test_memory_validation_flags_unresolved_references_honestly():
    records = [{"memory_health": {"unresolved_event_references": 3}}]
    report = validate_market_memory(records)
    assert report["structure_uses_history"] is False


def test_memory_validation_never_raises_on_missing_telemetry():
    report = validate_market_memory([{}])
    assert report["event_growth_rate"] is None
    assert report["regime_duration_cycles"] is None
