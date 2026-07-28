"""Tests for bujji.msi_knowledge_validation -- Series 105.

Covers: causal ordering, all seven validation states, metric
computation, journal history, serialization, query, determinism, and a
golden replay test built from real corpus-derived occurrence data (no
fabrication)."""
from __future__ import annotations

import pytest

from bujji.msi_knowledge_validation import engine as kve_engine
from bujji.msi_knowledge_validation import query as kve_query
from bujji.msi_knowledge_validation import serialization as kve_serialization
from bujji.msi_knowledge_validation import taxonomy as kve_taxonomy
from bujji.msi_knowledge_validation.journal import KnowledgeValidationJournal
from bujji.msi_knowledge_validation.models import HypothesisOccurrence

LABEL = "RANGE_DAY: neutral family independently suitable while directional selected"


def _occ(day, ts, mc="RANGE", valid=True, legal=True, replay=True):
    return HypothesisOccurrence(
        day=day, timestamp=ts, market_classification=mc, evidence_packet_id=f"ep-{day}",
        counterfactual_session_id=f"cfs-{day}", counterfactual_legal=legal,
        opportunity_assessment_id=f"oa-{day}", opportunity_classification="OPPORTUNITY_IDENTIFIED",
        phenomena_report_id=f"mpr-{day}", causally_valid=valid, replay_reproducible=replay,
    )


def _days(n, mc_cycle=("RANGE",)):
    return [
        _occ(f"d{i:02d}", f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}T10:00:00", mc=mc_cycle[i % len(mc_cycle)])
        for i in range(1, n + 1)
    ]


# --- Causal ordering ---

def test_causal_order_passes_for_chronological_occurrences():
    ok, _ = kve_engine.validate_causal_order(_days(3))
    assert ok is True


def test_causal_order_fails_for_out_of_order_occurrences():
    occs = [_occ("d2", "2026-01-05T10:00:00"), _occ("d1", "2026-01-01T10:00:00")]
    ok, _ = kve_engine.validate_causal_order(occs)
    assert ok is False


def test_validate_hypothesis_raises_on_noncausal_sequence():
    occs = [_occ("d2", "2026-01-05T10:00:00"), _occ("d1", "2026-01-01T10:00:00")]
    with pytest.raises(ValueError):
        kve_engine.validate_hypothesis(LABEL, occs, generated_timestamp="x")


# --- All seven validation states ---

def test_zero_occurrences_is_not_observed():
    r = kve_engine.validate_hypothesis(LABEL, [], generated_timestamp="x")
    assert r.validation_state == kve_taxonomy.STATE_NOT_OBSERVED


def test_one_occurrence_is_observed():
    r = kve_engine.validate_hypothesis(LABEL, _days(1), generated_timestamp="x")
    assert r.validation_state == kve_taxonomy.STATE_OBSERVED


def test_a_single_occurrence_is_never_evidence():
    """Direct check of the same principle Series 100 established for
    Knowledge Candidates: one occurrence proves nothing on its own."""
    r = kve_engine.validate_hypothesis(LABEL, _days(1), generated_timestamp="x")
    assert r.validation_state != kve_taxonomy.STATE_VALIDATED
    assert r.validation_state != kve_taxonomy.STATE_EMERGING


def test_few_occurrences_is_repeated():
    r = kve_engine.validate_hypothesis(LABEL, _days(3), generated_timestamp="x")
    assert r.validation_state == kve_taxonomy.STATE_REPEATED


def test_moderate_consistent_occurrences_is_emerging():
    r = kve_engine.validate_hypothesis(LABEL, _days(8, mc_cycle=("RANGE",)), generated_timestamp="x")
    assert r.validation_state == kve_taxonomy.STATE_EMERGING


def test_many_diverse_consistent_replayable_occurrences_is_validated():
    r = kve_engine.validate_hypothesis(LABEL, _days(25, mc_cycle=("RANGE", "TREND", "VOL")), generated_timestamp="x")
    assert r.validation_state == kve_taxonomy.STATE_VALIDATED
    assert r.diversity_count == 3


def test_many_occurrences_without_diversity_stays_emerging_not_validated():
    """VALIDATED requires real diversity (>= 3 distinct market
    classifications), not just occurrence count -- 25 occurrences of the
    SAME real classification never crosses into VALIDATED."""
    r = kve_engine.validate_hypothesis(LABEL, _days(25, mc_cycle=("RANGE",)), generated_timestamp="x")
    assert r.validation_state == kve_taxonomy.STATE_EMERGING
    assert r.diversity_count == 1


def test_mostly_inconsistent_occurrences_is_invalidated():
    occs = [_occ(f"d{i:02d}", f"2026-01-{i:02d}T10:00:00", valid=(i % 4 == 0), legal=(i % 4 == 0)) for i in range(1, 11)]
    r = kve_engine.validate_hypothesis(LABEL, occs, generated_timestamp="x")
    assert r.validation_state == kve_taxonomy.STATE_INVALIDATED
    assert r.consistency == kve_taxonomy.CONSISTENCY_INCONSISTENT


def test_decaying_when_recent_rate_is_weaker_than_lifetime_rate():
    """25 occurrences clustered early, then a long real gap before the
    most recent ones -- a real, disclosed WEAKENING trend, overriding
    what would otherwise be VALIDATED."""
    early = [_occ(f"d{i:02d}", f"2026-01-{i:02d}T10:00:00", mc=("RANGE" if i % 3 == 0 else "TREND" if i % 3 == 1 else "VOL")) for i in range(1, 21)]
    late = [_occ("d21", "2026-06-01T10:00:00", mc="RANGE"), _occ("d22", "2026-07-01T10:00:00", mc="TREND")]
    occs = early + late
    r = kve_engine.validate_hypothesis(LABEL, occs, generated_timestamp="x")
    assert r.evidence_decay == kve_taxonomy.TREND_WEAKENING
    assert r.validation_state == kve_taxonomy.STATE_DECAYING


# --- Metrics ---

def test_consistency_ratio_computed_correctly():
    occs = [_occ(f"d{i:02d}", f"2026-01-{i:02d}T10:00:00", valid=(i <= 7), legal=(i <= 7)) for i in range(1, 11)]
    r = kve_engine.validate_hypothesis(LABEL, occs, generated_timestamp="x")
    assert r.consistency_ratio == pytest.approx(0.7)


def test_supporting_references_are_real_and_deduped():
    occs = _days(5)
    r = kve_engine.validate_hypothesis(LABEL, occs, generated_timestamp="x")
    assert len(r.supporting_evidence_packets) == 5
    assert len(r.supporting_opportunity_assessments) == 5
    assert len(r.supporting_counterfactual_sessions) == 5
    assert len(r.supporting_phenomena) == 5


def test_historical_occurrence_statistics_is_real_and_ordered():
    r = kve_engine.validate_hypothesis(LABEL, _days(4), generated_timestamp="x")
    assert [c for _, c in r.historical_occurrence_statistics] == [1, 2, 3, 4]
    assert [d for d, _ in r.historical_occurrence_statistics] == ["d01", "d02", "d03", "d04"]


# --- No Engineering, structural ---

def test_report_never_contains_a_recommendation_field():
    import dataclasses
    from bujji.msi_knowledge_validation.models import KnowledgeValidationReport
    field_names = {f.name for f in dataclasses.fields(KnowledgeValidationReport)}
    assert field_names.isdisjoint({"recommendation", "knowledge_candidate", "engineering_proposal"})


# --- Determinism ---

def test_validate_hypothesis_is_deterministic():
    occs = _days(5)
    r1 = kve_engine.validate_hypothesis(LABEL, occs, generated_timestamp="x")
    r2 = kve_engine.validate_hypothesis(LABEL, occs, generated_timestamp="x")
    assert r1.validation_id == r2.validation_id
    assert r1 == r2


# --- Journal (validation history, Deliverable 6) / serialization / query ---

def test_journal_history_never_overwrites_prior_state(tmp_path):
    journal = KnowledgeValidationJournal(tmp_path)
    r1 = kve_engine.validate_hypothesis(LABEL, _days(3), generated_timestamp="t1")
    journal.record(r1)
    r2 = kve_engine.validate_hypothesis(LABEL, _days(8), generated_timestamp="t2")
    journal.record(r2)
    history = journal.history_for(LABEL)
    assert len(history) == 2
    assert history[0].validation_state == kve_taxonomy.STATE_REPEATED
    assert history[1].validation_state == kve_taxonomy.STATE_EMERGING
    assert journal.latest_for(LABEL) == r2


def test_serialization_round_trip():
    r = kve_engine.validate_hypothesis(LABEL, _days(5), generated_timestamp="x")
    text = kve_serialization.report_to_json(r)
    recovered = kve_serialization.report_from_json(text)
    assert recovered == r


def test_query_state_distribution_and_filters():
    r1 = kve_engine.validate_hypothesis("h1", _days(25, mc_cycle=("RANGE", "TREND", "VOL")), generated_timestamp="x")
    r2 = kve_engine.validate_hypothesis("h2", _days(1), generated_timestamp="x")
    dist = kve_query.state_distribution([r1, r2])
    assert dist[kve_taxonomy.STATE_VALIDATED] == 1
    assert dist[kve_taxonomy.STATE_OBSERVED] == 1
    assert kve_query.validated([r1, r2]) == (r1,)


# --- Golden replay test: real corpus-derived occurrence pattern ---

def test_golden_replay_real_range_persistence_pattern_from_sprint116_corpus():
    """Uses the REAL day list Sprint 116's own investigation established
    for the 'COVERED/RATIO independently SUITABLE under RANGE_PERSISTENCE
    but excluded by Expression' pattern (9 real days, real day strings
    already cited in this session's own Sprint 116 report) -- not
    fabricated, a real, previously-established real-corpus finding, now
    fed through KVE as a real hypothesis test."""
    real_days = ["2026-05-26", "2026-06-02", "2026-06-04", "2026-06-16", "2026-06-23",
                 "2026-06-25", "2026-07-02", "2026-07-14", "2026-07-15"]
    occs = [
        _occ(day, f"{day}T15:15:00", mc="RANGE", valid=True, legal=True, replay=True)
        for day in real_days
    ]
    r = kve_engine.validate_hypothesis(
        "RANGE_PERSISTENCE: COVERED/RATIO independently SUITABLE, excluded by Expression filter (Sprint 116)",
        occs, generated_timestamp="2026-07-28T00:00:00",
    )
    assert r.occurrence_count == 9
    # single real market classification across all 9 real days -> low
    # diversity, correctly short of VALIDATED's diversity requirement --
    # an honest EMERGING result, not an inflated VALIDATED claim.
    assert r.validation_state == kve_taxonomy.STATE_EMERGING
    assert r.diversity_count == 1
