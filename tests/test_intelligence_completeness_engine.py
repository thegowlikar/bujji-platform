"""Tests -- Intelligence Completeness Engine, Phase 9 Upgrade 2. Pure
functions over plain dict records; no broker, no live calls, no
coupling to any decision-making code."""
from __future__ import annotations

import json

from bujji.intelligence_completeness.engine import (
    ALL_TRACKED_DOMAINS, evaluate_cycle, evaluate_session,
)
from bujji.intelligence_completeness.models import (
    STATUS_COMPLETE, STATUS_PARTIAL, STATUS_UNKNOWN,
)


def _empty_record(ts="2026-08-06T09:15:00+05:30"):
    return {"timestamp": ts, **{d: None for d in ALL_TRACKED_DOMAINS}}


def test_all_domains_unknown_on_a_fully_empty_record():
    report = evaluate_cycle(_empty_record())
    assert len(report.domains) == len(ALL_TRACKED_DOMAINS)
    for d in report.domains:
        assert d.status == STATUS_UNKNOWN
        assert d.reason  # never an empty/fabricated reason
    assert report.completeness_score == 0.0


def test_honest_unknown_scores_equal_or_better_than_would_be_expected():
    """The core philosophy requirement: an honestly UNKNOWN domain must
    never be penalized on the honesty axis -- only completeness reflects
    it being unresolved."""
    report = evaluate_cycle(_empty_record())
    assert report.honesty_score == 100.0  # no contradictions disclosed anywhere -- fully honest.


def test_high_confidence_domain_reports_complete():
    record = _empty_record()
    record["price_structure"] = {"confidence": "HIGH"}
    report = evaluate_cycle(record)
    ps = next(d for d in report.domains if d.domain == "price_structure")
    assert ps.status == STATUS_COMPLETE
    assert report.completeness_score > 0.0


def test_moderate_confidence_domain_reports_partial():
    record = _empty_record()
    record["market_structure"] = {"confidence": "MODERATE"}
    report = evaluate_cycle(record)
    ms = next(d for d in report.domains if d.domain == "market_structure")
    assert ms.status == STATUS_PARTIAL


def test_consensus_level_mapping():
    record = _empty_record()
    record["consensus"] = {"consensus_level": "NO_CONSENSUS"}
    report = evaluate_cycle(record)
    c = next(d for d in report.domains if d.domain == "consensus")
    assert c.status == STATUS_UNKNOWN
    record["consensus"] = {"consensus_level": "STRONG_CONSENSUS"}
    report = evaluate_cycle(record)
    c = next(d for d in report.domains if d.domain == "consensus")
    assert c.status == STATUS_COMPLETE


def test_liquidity_tightness_mapping():
    record = _empty_record()
    record["liquidity"] = {"tightness": "TIGHT", "reason": "combined spread 0.30% <= threshold 0.5%"}
    report = evaluate_cycle(record)
    liq = next(d for d in report.domains if d.domain == "liquidity")
    assert liq.status == STATUS_COMPLETE

    record["liquidity"] = {"tightness": "UNKNOWN", "reason": "invalid_quote: ce_bid is missing or non-positive"}
    report = evaluate_cycle(record)
    liq = next(d for d in report.domains if d.domain == "liquidity")
    assert liq.status == STATUS_UNKNOWN
    assert "invalid_quote" in liq.reason


def test_participant_positioning_uses_its_own_weak_moderate_strong_vocabulary():
    """participant_positioning's `positioning_strength` field uses
    UNKNOWN/WEAK/MODERATE/STRONG -- a genuinely different vocabulary
    from the NONE/LOW/MODERATE/HIGH convention used elsewhere. A real
    'WEAK' reading must map to PARTIAL, never be misclassified as
    UNKNOWN by conflating it with the other vocabulary (caught via a
    spot-check against a real live cycle record during development)."""
    record = _empty_record()
    record["participant_positioning"] = {"positioning_strength": "WEAK"}
    report = evaluate_cycle(record)
    pp = next(d for d in report.domains if d.domain == "participant_positioning")
    assert pp.status == STATUS_PARTIAL
    record["participant_positioning"] = {"positioning_strength": "STRONG"}
    report = evaluate_cycle(record)
    pp = next(d for d in report.domains if d.domain == "participant_positioning")
    assert pp.status == STATUS_COMPLETE


def test_completeness_score_never_reads_strategy_selection_outcome():
    """A confident selected_strategy_family must not, by itself, inflate
    completeness beyond what strategy_selection's OWN confidence field
    says -- the score must not reward "more trades"."""
    record_a = _empty_record()
    record_a["strategy_selection"] = {"selected_strategy_family": "IRON_FLY", "confidence": "LOW"}
    record_b = _empty_record()
    record_b["strategy_selection"] = {"selected_strategy_family": None, "confidence": "LOW"}
    a = evaluate_cycle(record_a)
    b = evaluate_cycle(record_b)
    assert a.completeness_score == b.completeness_score  # identical -- only confidence matters, not the pick.


def test_complete_status_with_disclosed_contradiction_penalizes_honesty():
    """The one dishonesty pattern this module actually detects: claiming
    a COMPLETE (HIGH-confidence) read while the same domain's own
    assessment disclosed an internal contradiction."""
    record = _empty_record()
    record["market_structure"] = {"confidence": "HIGH", "contradictions": ("support_and_resistance_both_active",)}
    report = evaluate_cycle(record)
    assert report.honesty_score < 100.0


def test_partial_or_unknown_with_contradiction_not_penalized():
    """Disclosing a contradiction while ALSO honestly reporting
    PARTIAL/UNKNOWN confidence is not the violation this guards against
    -- it's just more honest disclosure, never penalized."""
    record = _empty_record()
    record["market_structure"] = {"confidence": "LOW", "contradictions": ("support_and_resistance_both_active",)}
    report = evaluate_cycle(record)
    assert report.honesty_score == 100.0


def test_evaluate_session_maps_over_records():
    records = [_empty_record(ts=f"2026-08-06T09:{15+i}:00+05:30") for i in range(3)]
    reports = evaluate_session(records)
    assert len(reports) == 3
    assert all(r.completeness_score == 0.0 for r in reports)


def test_report_json_serializable():
    report = evaluate_cycle(_empty_record())
    json.dumps(report.to_dict())


def test_never_raises_on_malformed_domain_value():
    """A domain value that isn't even a dict (unexpected shape) must
    degrade to UNKNOWN, never crash the whole cycle's report."""
    record = _empty_record()
    record["price_structure"] = "not_a_dict"
    try:
        evaluate_cycle(record)
        raised = False
    except AttributeError:
        raised = True
    assert not raised, "evaluate_cycle must not crash on a malformed domain value"
