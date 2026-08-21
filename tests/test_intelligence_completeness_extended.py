"""Tests -- Intelligence Completeness Expansion, Phase 11 Upgrade 4.
Pure functions over plain dicts; additive on top of evaluate_cycle()."""
from __future__ import annotations

import json

from bujji.intelligence_completeness.engine import (
    ALL_TRACKED_DOMAINS, EXPANDED_DOMAINS, evaluate_cycle, evaluate_cycle_extended,
)
from bujji.intelligence_completeness.models import STATUS_COMPLETE, STATUS_PARTIAL, STATUS_UNKNOWN


def _empty_record(ts="2026-08-06T09:15:00+05:30"):
    return {"timestamp": ts, **{d: None for d in ALL_TRACKED_DOMAINS}}


def test_extended_with_no_new_inputs_has_four_extra_unknown_domains():
    report = evaluate_cycle_extended(_empty_record())
    assert len(report.domains) == len(ALL_TRACKED_DOMAINS) + len(EXPANDED_DOMAINS)
    for d in EXPANDED_DOMAINS:
        found = next(x for x in report.domains if x.domain == d)
        assert found.status == STATUS_UNKNOWN


def test_extended_never_mutates_base_domain_results():
    """The original 9 domains' status must be byte-identical between
    evaluate_cycle() and evaluate_cycle_extended()."""
    record = _empty_record()
    record["price_structure"] = {"confidence": "HIGH"}
    base = evaluate_cycle(record)
    extended = evaluate_cycle_extended(record)
    base_by_name = {d.domain: d.status for d in base.domains}
    extended_by_name = {d.domain: d.status for d in extended.domains if d.domain in base_by_name}
    assert base_by_name == extended_by_name


def test_memory_completeness_complete_when_fully_resolved():
    memory_health = {"historical_events_available": 100, "unresolved_event_references": 0, "history_resolution_ratio": 1.0}
    report = evaluate_cycle_extended(_empty_record(), memory_health=memory_health)
    d = next(x for x in report.domains if x.domain == "memory_completeness")
    assert d.status == STATUS_COMPLETE


def test_memory_completeness_partial_when_unresolved_references_exist():
    memory_health = {"historical_events_available": 100, "unresolved_event_references": 3, "history_resolution_ratio": 0.97}
    report = evaluate_cycle_extended(_empty_record(), memory_health=memory_health)
    d = next(x for x in report.domains if x.domain == "memory_completeness")
    assert d.status == STATUS_PARTIAL


def test_memory_completeness_unknown_with_no_history_yet():
    memory_health = {"historical_events_available": 0, "unresolved_event_references": 0, "history_resolution_ratio": 1.0}
    report = evaluate_cycle_extended(_empty_record(), memory_health=memory_health)
    d = next(x for x in report.domains if x.domain == "memory_completeness")
    assert d.status == STATUS_UNKNOWN


def test_regime_completeness_maps_stability_states():
    for stability, expected in (
        ("INSUFFICIENT_HISTORY", STATUS_UNKNOWN),
        ("FRAGILE", STATUS_PARTIAL),
        ("WEAKENING", STATUS_PARTIAL),
        ("STABLE", STATUS_COMPLETE),
    ):
        report = evaluate_cycle_extended(_empty_record(), regime_memory={"stability": stability})
        d = next(x for x in report.domains if x.domain == "regime_completeness")
        assert d.status == expected, f"{stability} -> expected {expected}, got {d.status}"


def test_narrative_completeness_downgraded_by_contradictions():
    """HIGH confidence would normally be COMPLETE, but a disclosed
    contradiction must downgrade it -- never claim full alignment while
    flagging a real disagreement."""
    narrative = {"confidence": "HIGH", "contradictions": ["price direction without participant confirmation"]}
    report = evaluate_cycle_extended(_empty_record(), narrative=narrative)
    d = next(x for x in report.domains if x.domain == "narrative_completeness")
    assert d.status == STATUS_PARTIAL


def test_narrative_completeness_complete_when_high_confidence_no_contradictions():
    narrative = {"confidence": "HIGH", "contradictions": []}
    report = evaluate_cycle_extended(_empty_record(), narrative=narrative)
    d = next(x for x in report.domains if x.domain == "narrative_completeness")
    assert d.status == STATUS_COMPLETE


def test_data_quality_completeness_complete_on_healthy_snapshot():
    record = _empty_record()
    record["market_snapshot_health"] = "OK"
    record["market_snapshot_missing_fields"] = []
    record["liquidity"] = {"data_quality": "SUFFICIENT"}
    report = evaluate_cycle_extended(record)
    d = next(x for x in report.domains if x.domain == "data_quality_completeness")
    assert d.status == STATUS_COMPLETE


def test_data_quality_completeness_partial_on_missing_fields():
    record = _empty_record()
    record["market_snapshot_health"] = "DEGRADED"
    record["market_snapshot_missing_fields"] = ["vix"]
    report = evaluate_cycle_extended(record)
    d = next(x for x in report.domains if x.domain == "data_quality_completeness")
    assert d.status == STATUS_PARTIAL


def test_data_quality_completeness_unknown_when_snapshot_unavailable():
    record = _empty_record()
    record["market_snapshot_health"] = "UNAVAILABLE"
    report = evaluate_cycle_extended(record)
    d = next(x for x in report.domains if x.domain == "data_quality_completeness")
    assert d.status == STATUS_UNKNOWN


def test_completeness_score_never_reads_strategy_selection_outcome_extended():
    record_a = _empty_record()
    record_a["strategy_selection"] = {"selected_strategy_family": "IRON_FLY", "confidence": "LOW"}
    record_b = _empty_record()
    record_b["strategy_selection"] = {"selected_strategy_family": None, "confidence": "LOW"}
    a = evaluate_cycle_extended(record_a)
    b = evaluate_cycle_extended(record_b)
    assert a.completeness_score == b.completeness_score


def test_report_json_serializable():
    report = evaluate_cycle_extended(
        _empty_record(),
        memory_health={"historical_events_available": 10, "unresolved_event_references": 0},
        regime_memory={"stability": "STABLE"},
        narrative={"confidence": "MODERATE", "contradictions": []},
    )
    json.dumps(report.to_dict())
