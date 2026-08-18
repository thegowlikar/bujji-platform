"""Tests -- Market Narrative Engine, Phase 11 Upgrade 2. Pure functions
over plain dicts, no LLM, no broker, no live calls."""
from __future__ import annotations

import json

from bujji.market_narrative.engine import build_narrative


def test_empty_record_produces_honest_insufficient_evidence_story():
    report = build_narrative({})
    assert "Insufficient evidence" in report.market_story
    assert report.dominant_factors == ()
    assert report.confidence == "NONE"


def test_compression_fragment_included_when_confirmed():
    record = {"volatility_structure": {"compression_state": "CONFIRMED", "confidence": "MODERATE"}}
    report = build_narrative(record)
    assert "compressed" in report.market_story.lower()
    assert "compression" in report.dominant_factors


def test_unresolved_domain_never_fabricates_a_sentence():
    """A None market_structure must contribute nothing -- never a
    fabricated 'price is near support' with no real evidence."""
    record = {"market_structure": None}
    report = build_narrative(record)
    assert "support" not in report.market_story.lower()
    assert "resistance" not in report.market_story.lower()


def test_structure_location_inside_range_fragment():
    record = {"market_structure": {"structure_location": "INSIDE_RANGE", "confidence": "HIGH"}}
    report = build_narrative(record)
    assert "structural range" in report.market_story
    assert "range_bound_structure" in report.dominant_factors


def test_no_consensus_fragment_and_confidence_reflects_it():
    record = {"consensus": {"consensus_level": "NO_CONSENSUS"}}
    report = build_narrative(record)
    assert "weak" in report.market_story.lower()
    assert "weak_directional_consensus" in report.dominant_factors


def test_regime_memory_fragment_included_when_provided():
    record = {"volatility_structure": {"compression_state": "CONFIRMED"}}
    regime_memory = {"current_regime": "RANGING", "duration_cycles": 12}
    report = build_narrative(record, regime_memory)
    assert "ranging regime" in report.market_story.lower()
    assert "regime_context" in report.dominant_factors


def test_regime_memory_omitted_when_not_provided():
    record = {"volatility_structure": {"compression_state": "CONFIRMED"}}
    report = build_narrative(record, None)
    assert "regime_context" not in report.dominant_factors


def test_contradiction_price_direction_without_participant_confirmation():
    record = {
        "market_direction": {"overall_direction": "BULLISH", "overall_confidence": "MODERATE"},
        "participant_positioning": {"positioning_bias": "NEUTRAL"},
    }
    report = build_narrative(record)
    assert "price direction without participant confirmation" in report.contradictions


def test_no_contradiction_when_direction_and_positioning_agree():
    record = {
        "market_direction": {"overall_direction": "BULLISH", "overall_confidence": "MODERATE"},
        "participant_positioning": {"positioning_bias": "BULLISH_POSITIONING"},
    }
    report = build_narrative(record)
    assert "price direction without participant confirmation" not in report.contradictions


def test_high_confidence_despite_no_consensus_is_flagged():
    record = {
        "market_direction": {"overall_direction": "BULLISH", "overall_confidence": "HIGH"},
        "consensus": {"consensus_level": "NO_CONSENSUS"},
    }
    report = build_narrative(record)
    assert "high directional confidence despite no cross-domain consensus" in report.contradictions


def test_upstream_contradictions_passed_through_with_prefix():
    record = {"market_structure": {"structure_location": "INSIDE_RANGE", "contradictions": ("support_and_resistance_both_active",)}}
    report = build_narrative(record)
    assert "market_structure: support_and_resistance_both_active" in report.contradictions


def test_report_json_serializable():
    record = {"volatility_structure": {"compression_state": "CONFIRMED", "confidence": "HIGH"}}
    report = build_narrative(record)
    json.dumps(report.to_dict())


def test_never_raises_on_malformed_domain_value():
    record = {"market_structure": "not_a_dict", "consensus": 42}
    try:
        build_narrative(record)
        raised = False
    except Exception:
        raised = True
    assert not raised
