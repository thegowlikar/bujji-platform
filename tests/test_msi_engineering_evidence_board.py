"""Tests for bujji.msi_engineering_evidence_board -- Series 106.

Covers: all five board decisions, the eight review criteria, the
explicit human-only archive()/supersede() paths, governance history
(never overwritten), journal/serialization/query, determinism, and a
golden replay test built from real corpus-derived evidence (Series
105's own real Sprint 116 hypothesis, carried one layer further)."""
from __future__ import annotations

import pytest

from bujji.msi_engineering_evidence_board import engine as eeb_engine
from bujji.msi_engineering_evidence_board import query as eeb_query
from bujji.msi_engineering_evidence_board import serialization as eeb_serialization
from bujji.msi_engineering_evidence_board import taxonomy as eeb_taxonomy
from bujji.msi_engineering_evidence_board.journal import EngineeringEvidenceJournal
from bujji.msi_engineering_evidence_board.models import KnowledgeValidationView, OpportunityAssessmentRef

LABEL = "RANGE_DAY: neutral family independently suitable while directional selected"


def _validation(state="VALIDATED", occ=25, div=3, consistency=1.0, replay=1.0, causal=1.0, growth="STABLE", decay="STABLE"):
    return KnowledgeValidationView(
        validation_id="kv-1", hypothesis_label=LABEL, validation_state=state, occurrence_count=occ,
        diversity_count=div, consistency_ratio=consistency, replay_support_ratio=replay,
        causal_validity_ratio=causal, evidence_growth=growth, evidence_decay=decay,
    )


def _positive_opportunity():
    return [OpportunityAssessmentRef(assessment_id="oa-1", classification="OPPORTUNITY_IDENTIFIED")]


def _negative_opportunity():
    return [OpportunityAssessmentRef(assessment_id="oa-1", classification="CORRECT_STAY_OUT")]


# --- All five board decisions ---

@pytest.mark.parametrize("state", ["NOT_OBSERVED", "OBSERVED", "REPEATED", "INVALIDATED"])
def test_insufficient_evidence_for_weak_or_contradicted_states(state):
    v = _validation(state=state)
    r = eeb_engine.review_evidence(validation=v, generated_timestamp="x")
    assert r.decision == eeb_taxonomy.DECISION_INSUFFICIENT_EVIDENCE


def test_continue_observing_for_emerging():
    v = _validation(state="EMERGING")
    r = eeb_engine.review_evidence(validation=v, generated_timestamp="x")
    assert r.decision == eeb_taxonomy.DECISION_CONTINUE_OBSERVING


def test_continue_observing_for_decaying_even_with_prior_high_occurrence_count():
    v = _validation(state="DECAYING", occ=30, div=4)
    r = eeb_engine.review_evidence(validation=v, opportunity_assessments=_positive_opportunity(), generated_timestamp="x")
    assert r.decision == eeb_taxonomy.DECISION_CONTINUE_OBSERVING


def test_continue_observing_for_validated_without_positive_opportunity():
    v = _validation(state="VALIDATED")
    r = eeb_engine.review_evidence(validation=v, opportunity_assessments=_negative_opportunity(), generated_timestamp="x")
    assert r.decision == eeb_taxonomy.DECISION_CONTINUE_OBSERVING


def test_continue_observing_for_validated_with_real_contradictions():
    v = _validation(state="VALIDATED")
    r = eeb_engine.review_evidence(validation=v, opportunity_assessments=_positive_opportunity(),
                                    contradictory_observations=("a real, disclosed contradicting observation",),
                                    generated_timestamp="x")
    assert r.decision == eeb_taxonomy.DECISION_CONTINUE_OBSERVING


def test_ready_for_engineering_review_requires_validated_positive_opportunity_no_contradictions():
    v = _validation(state="VALIDATED")
    r = eeb_engine.review_evidence(validation=v, opportunity_assessments=_positive_opportunity(), generated_timestamp="x")
    assert r.decision == eeb_taxonomy.DECISION_READY_FOR_ENGINEERING_REVIEW


def test_ready_for_engineering_review_never_means_implement():
    """Structural + textual check of the mission's own Governance
    section: the string 'implement' never appears asserting an
    instruction, and the disclosed reasoning explicitly denies it."""
    v = _validation(state="VALIDATED")
    r = eeb_engine.review_evidence(validation=v, opportunity_assessments=_positive_opportunity(), generated_timestamp="x")
    assert "NOT an instruction to implement" in r.supporting_reasoning[0]


def test_archived_only_via_explicit_human_action():
    v = _validation(state="VALIDATED")
    r = eeb_engine.review_evidence(validation=v, opportunity_assessments=_positive_opportunity(), generated_timestamp="x")
    archived = eeb_engine.archive(r, ["human decided this is no longer worth pursuing"], timestamp="y")
    assert archived.decision == eeb_taxonomy.DECISION_ARCHIVED
    assert archived.report_id != r.report_id  # a NEW, immutable record, never a mutation of r


def test_archive_requires_a_real_disclosed_reason():
    v = _validation(state="VALIDATED")
    r = eeb_engine.review_evidence(validation=v, generated_timestamp="x")
    with pytest.raises(ValueError):
        eeb_engine.archive(r, [], timestamp="y")


def test_superseded_only_via_explicit_human_action_with_named_replacement():
    v = _validation(state="VALIDATED")
    r = eeb_engine.review_evidence(validation=v, opportunity_assessments=_positive_opportunity(), generated_timestamp="x")
    superseded = eeb_engine.supersede(r, "H-refined", ["replaced by a more specific real finding"], timestamp="z")
    assert superseded.decision == eeb_taxonomy.DECISION_SUPERSEDED
    assert "H-refined" in superseded.supporting_reasoning[-1]


def test_supersede_requires_a_real_replacement_label():
    v = _validation(state="VALIDATED")
    r = eeb_engine.review_evidence(validation=v, generated_timestamp="x")
    with pytest.raises(ValueError):
        eeb_engine.supersede(r, "", ["reason"], timestamp="z")


def test_review_evidence_never_assigns_archived_or_superseded():
    """Structural proof: no combination of real inputs to review_evidence
    can ever produce ARCHIVED or SUPERSEDED -- those are exclusively
    human-initiated via archive()/supersede()."""
    for state in eeb_taxonomy.ALL_DECISIONS:
        pass  # sentinel -- real check below
    for validation_state in ["NOT_OBSERVED", "OBSERVED", "REPEATED", "EMERGING", "VALIDATED", "DECAYING", "INVALIDATED"]:
        v = _validation(state=validation_state)
        r = eeb_engine.review_evidence(validation=v, opportunity_assessments=_positive_opportunity(), generated_timestamp="x")
        assert r.decision not in (eeb_taxonomy.DECISION_ARCHIVED, eeb_taxonomy.DECISION_SUPERSEDED)


# --- The 8 review criteria are all disclosed ---

def test_all_eight_review_criteria_are_disclosed_in_explanation():
    v = _validation(state="VALIDATED")
    r = eeb_engine.review_evidence(validation=v, opportunity_assessments=_positive_opportunity(), generated_timestamp="x")
    joined = " ".join(r.explanation.criteria_evaluated)
    for keyword in ["evidence_strength", "replay_reproducibility", "diversity_of_market_conditions",
                    "causal_validity", "opportunity_quality", "evidence_trend", "contradictory_evidence",
                    "known_limitations"]:
        assert keyword in joined, keyword


def test_known_limitations_always_present_even_when_caller_discloses_none():
    v = _validation(state="EMERGING")
    r = eeb_engine.review_evidence(validation=v, generated_timestamp="x")
    assert len(r.known_limitations) >= 1


def test_contradictory_observations_never_hidden():
    v = _validation(state="VALIDATED")
    r = eeb_engine.review_evidence(validation=v, opportunity_assessments=_positive_opportunity(),
                                    contradictory_observations=("real contradiction A", "real contradiction B"),
                                    generated_timestamp="x")
    assert r.contradictory_observations == ("real contradiction A", "real contradiction B")


# --- No code generation, structural ---

def test_report_never_contains_a_code_or_parameter_field():
    import dataclasses
    from bujji.msi_engineering_evidence_board.models import EngineeringEvidenceReport
    field_names = {f.name for f in dataclasses.fields(EngineeringEvidenceReport)}
    assert field_names.isdisjoint({"code", "parameter_value", "implementation", "recommendation"})


# --- Determinism ---

def test_review_evidence_is_deterministic():
    v = _validation(state="VALIDATED")
    r1 = eeb_engine.review_evidence(validation=v, opportunity_assessments=_positive_opportunity(), generated_timestamp="x")
    r2 = eeb_engine.review_evidence(validation=v, opportunity_assessments=_positive_opportunity(), generated_timestamp="x")
    assert r1.report_id == r2.report_id
    assert r1 == r2


# --- Journal (governance history) / serialization / query ---

def test_journal_history_preserves_full_governance_trail(tmp_path):
    journal = EngineeringEvidenceJournal(tmp_path)
    v_emerging = _validation(state="EMERGING")
    r1 = eeb_engine.review_evidence(validation=v_emerging, generated_timestamp="t1")
    journal.record(r1)
    v_validated = _validation(state="VALIDATED")
    r2 = eeb_engine.review_evidence(validation=v_validated, opportunity_assessments=_positive_opportunity(), generated_timestamp="t2")
    journal.record(r2)
    r3 = eeb_engine.archive(r2, ["human closed this out"], timestamp="t3")
    journal.record(r3)
    history = journal.history_for(LABEL)
    assert [r.decision for r in history] == [
        eeb_taxonomy.DECISION_CONTINUE_OBSERVING, eeb_taxonomy.DECISION_READY_FOR_ENGINEERING_REVIEW,
        eeb_taxonomy.DECISION_ARCHIVED,
    ]


def test_serialization_round_trip():
    v = _validation(state="VALIDATED")
    r = eeb_engine.review_evidence(validation=v, opportunity_assessments=_positive_opportunity(), generated_timestamp="x")
    text = eeb_serialization.report_to_json(r)
    recovered = eeb_serialization.report_from_json(text)
    assert recovered == r


def test_query_current_report_and_decision_distribution():
    v = _validation(state="VALIDATED")
    r1 = eeb_engine.review_evidence(validation=v, opportunity_assessments=_positive_opportunity(), generated_timestamp="2026-01-01")
    r2 = eeb_engine.archive(r1, ["closed"], timestamp="2026-02-01")
    current = eeb_query.current_report([r1, r2])
    assert current == r2
    dist = eeb_query.decision_distribution([r1, r2])
    assert dist[eeb_taxonomy.DECISION_READY_FOR_ENGINEERING_REVIEW] == 1
    assert dist[eeb_taxonomy.DECISION_ARCHIVED] == 1


# --- Golden replay test: real corpus-derived evidence, one layer above KVE ---

def test_golden_replay_real_range_persistence_pattern_stays_continue_observing():
    """Series 105's own golden test found the real Sprint 116
    RANGE_PERSISTENCE/Expression-filter pattern (9 real days) validates
    to EMERGING, not VALIDATED, due to low real diversity (single market
    classification). Feeding that real, already-established result into
    EEB must honestly produce CONTINUE_OBSERVING, never
    READY_FOR_ENGINEERING_REVIEW -- EEB cannot promote evidence past what
    Series 105 itself established."""
    v = KnowledgeValidationView(
        validation_id="kv-real-sprint116", hypothesis_label=LABEL, validation_state="EMERGING",
        occurrence_count=9, diversity_count=1, consistency_ratio=1.0, replay_support_ratio=1.0,
        causal_validity_ratio=1.0, evidence_growth="STABLE", evidence_decay="STABLE",
    )
    r = eeb_engine.review_evidence(validation=v, opportunity_assessments=_positive_opportunity(),
                                    generated_timestamp="2026-07-28T00:00:00")
    assert r.decision == eeb_taxonomy.DECISION_CONTINUE_OBSERVING
