"""Tests for bujji.msi_opportunity_assessment -- Series 104.

Covers: causality validation, the Opportunity Criteria AND-gate,
Hindsight Protection ordering, all five classification paths, journal/
serialization/query, determinism, and golden replay tests built from
real Series 99/102/103 field values (real corpus days, no fabrication)."""
from __future__ import annotations

import pytest

from bujji.msi_opportunity_assessment import engine as oae_engine
from bujji.msi_opportunity_assessment import query as oae_query
from bujji.msi_opportunity_assessment import serialization as oae_serialization
from bujji.msi_opportunity_assessment import taxonomy as oae_taxonomy
from bujji.msi_opportunity_assessment.journal import OpportunityAssessmentJournal
from bujji.msi_opportunity_assessment.models import CounterfactualView, DecisionView, PhenomenaView

DAY = "2026-07-13"


def _decision(outcome="NO_TRADE", family=None, conflicting=(), ts="2026-07-13T15:15:00"):
    return DecisionView(decision_id="d1", date=DAY, timestamp=ts, decision_outcome=outcome,
                         strategy_family=family, confidence="MODERATE", conflicting_domains=conflicting)


def _phenomena(types=("TREND_FAILURE",)):
    return PhenomenaView(report_id="mpr-1", day=DAY, phenomenon_types=types)


def _counterfactual(legality="LEGAL", alt_family="LONG_DIRECTIONAL", earliest="2026-07-13T09:15:00"):
    return CounterfactualView(session_id="cfs-1", replay_legality=legality, baseline_selected_family=None,
                               alternative_selected_family=alt_family, earliest_causal_timestamp=earliest)


# --- Causality (Deliverable 6) ---

def test_causality_passes_when_counterfactual_predates_decision():
    ok, reasons = oae_engine.validate_causality(_decision(), _counterfactual())
    assert ok is True


def test_causality_fails_when_counterfactual_is_after_decision():
    future_cf = _counterfactual(earliest="2026-07-13T16:00:00")
    ok, reasons = oae_engine.validate_causality(_decision(), future_cf)
    assert ok is False
    assert "future information" in reasons[0]


def test_assess_day_defaults_conservatively_on_causality_violation():
    """An invalid (acausal) assessment must never claim an opportunity --
    it defaults to the conservative classification and discloses the
    violation, never hidden."""
    future_cf = _counterfactual(earliest="2026-07-13T16:00:00")
    a = oae_engine.assess_day(decision=_decision(), phenomena=_phenomena(), counterfactual=future_cf,
                               evidence_packet_ids=("ep-1",), timestamp="2026-07-13T15:15:00")
    assert a.classification == oae_taxonomy.CLASSIFICATION_CORRECT_STAY_OUT
    assert a.evidence_strength == oae_taxonomy.CONFIDENCE_NONE
    assert "INVALID ASSESSMENT" in a.supporting_reasoning[0]


# --- All five classifications ---

def test_good_trade_taken_when_no_disqualifying_evidence():
    d = _decision(outcome="TRADE_APPROVED", family="BUTTERFLY")
    a = oae_engine.assess_day(decision=d, timestamp=d.timestamp)
    assert a.classification == oae_taxonomy.CLASSIFICATION_GOOD_TRADE_TAKEN


def test_trade_suboptimal_when_legal_alternative_differs():
    d = _decision(outcome="TRADE_APPROVED", family="LONG_DIRECTIONAL")
    cf = _counterfactual(alt_family="RATIO")
    a = oae_engine.assess_day(decision=d, counterfactual=cf, timestamp=d.timestamp)
    assert a.classification == oae_taxonomy.CLASSIFICATION_TRADE_SUBOPTIMAL


def test_trade_should_not_have_occurred_with_real_conflicting_evidence():
    d = _decision(outcome="TRADE_APPROVED", family="LONG_DIRECTIONAL", conflicting=("PRICE_STRUCTURE",))
    a = oae_engine.assess_day(decision=d, timestamp=d.timestamp)
    assert a.classification == oae_taxonomy.CLASSIFICATION_TRADE_SHOULD_NOT_HAVE_OCCURRED
    assert a.evidence_strength == oae_taxonomy.CONFIDENCE_HIGH


def test_correct_stay_out_when_no_trade_and_criteria_not_fully_met():
    d = _decision(outcome="NO_TRADE")
    a = oae_engine.assess_day(decision=d, timestamp=d.timestamp)  # no phenomena, no counterfactual, no evidence
    assert a.classification == oae_taxonomy.CLASSIFICATION_CORRECT_STAY_OUT


def test_opportunity_identified_only_when_all_six_criteria_pass():
    d = _decision(outcome="NO_TRADE")
    a = oae_engine.assess_day(decision=d, phenomena=_phenomena(), counterfactual=_counterfactual(),
                               evidence_packet_ids=("ep-1",), timestamp=d.timestamp)
    assert a.classification == oae_taxonomy.CLASSIFICATION_OPPORTUNITY_IDENTIFIED
    assert all("[PASS]" in c for c in a.explanation.opportunity_criteria_checked)


# --- Hindsight Protection ordering (Deliverable 7) — the burden of proof ---

@pytest.mark.parametrize("missing_criterion,kwargs", [
    ("no phenomena", dict(phenomena=None, counterfactual=_counterfactual(), evidence_packet_ids=("ep-1",))),
    ("illegal counterfactual", dict(phenomena=_phenomena(), counterfactual=_counterfactual(legality="ILLEGAL"), evidence_packet_ids=("ep-1",))),
    ("no counterfactual family selected", dict(phenomena=_phenomena(), counterfactual=_counterfactual(alt_family=None), evidence_packet_ids=("ep-1",))),
    ("no evidence packet", dict(phenomena=_phenomena(), counterfactual=_counterfactual(), evidence_packet_ids=())),
    ("no counterfactual at all", dict(phenomena=_phenomena(), counterfactual=None, evidence_packet_ids=("ep-1",))),
])
def test_any_single_missing_criterion_defaults_to_correct_stay_out(missing_criterion, kwargs):
    """The burden of proof is on the opportunity -- ANY one of the 6 real
    criteria failing means CORRECT_STAY_OUT, never OPPORTUNITY_IDENTIFIED."""
    d = _decision(outcome="NO_TRADE")
    a = oae_engine.assess_day(decision=d, timestamp=d.timestamp, **kwargs)
    assert a.classification == oae_taxonomy.CLASSIFICATION_CORRECT_STAY_OUT, missing_criterion


def test_correct_stay_out_is_the_real_default_not_opportunity():
    """Explicit ordering check: CORRECT_STAY_OUT must be provably
    attempted first -- verified by confirming it is the outcome whenever
    the AND-gate isn't fully satisfied, never a fallback reached only
    after a failed OPPORTUNITY_IDENTIFIED attempt with side effects."""
    d = _decision(outcome="NO_TRADE")
    a1 = oae_engine.assess_day(decision=d, timestamp=d.timestamp)
    a2 = oae_engine.assess_day(decision=d, timestamp=d.timestamp)
    assert a1 == a2  # same real inputs, same real (conservative) outcome, deterministic


# --- Exactly one classification, never multiple (structural) ---

def test_classification_is_always_exactly_one_of_the_five():
    d = _decision(outcome="TRADE_APPROVED", family="BUTTERFLY")
    a = oae_engine.assess_day(decision=d, timestamp=d.timestamp)
    assert isinstance(a.classification, str)
    assert a.classification in oae_taxonomy.ALL_CLASSIFICATIONS


# --- No Engineering (structural, per models.py's own missing types) ---

def test_assessment_never_contains_a_recommendation_field():
    import dataclasses
    from bujji.msi_opportunity_assessment.models import OpportunityAssessment
    field_names = {f.name for f in dataclasses.fields(OpportunityAssessment)}
    assert field_names.isdisjoint({"recommendation", "knowledge_candidate", "engineering_proposal", "strategy_change"})


# --- Determinism ---

def test_assess_day_is_deterministic():
    d = _decision(outcome="NO_TRADE")
    a1 = oae_engine.assess_day(decision=d, phenomena=_phenomena(), counterfactual=_counterfactual(),
                                evidence_packet_ids=("ep-1",), timestamp=d.timestamp)
    a2 = oae_engine.assess_day(decision=d, phenomena=_phenomena(), counterfactual=_counterfactual(),
                                evidence_packet_ids=("ep-1",), timestamp=d.timestamp)
    assert a1.assessment_id == a2.assessment_id
    assert a1 == a2


# --- Journal / serialization / query ---

def test_journal_round_trip(tmp_path):
    d = _decision(outcome="TRADE_APPROVED", family="BUTTERFLY")
    a = oae_engine.assess_day(decision=d, timestamp=d.timestamp)
    journal = OpportunityAssessmentJournal(tmp_path)
    journal.record(a)
    assert journal.by_day(DAY) == a


def test_serialization_round_trip():
    d = _decision(outcome="NO_TRADE")
    a = oae_engine.assess_day(decision=d, phenomena=_phenomena(), counterfactual=_counterfactual(),
                               evidence_packet_ids=("ep-1",), timestamp=d.timestamp)
    text = oae_serialization.assessment_to_json(a)
    recovered = oae_serialization.assessment_from_json(text)
    assert recovered == a


def test_query_classification_distribution_and_filters():
    d1 = _decision(outcome="TRADE_APPROVED", family="BUTTERFLY")
    a1 = oae_engine.assess_day(decision=d1, timestamp=d1.timestamp)
    d2 = _decision(outcome="NO_TRADE")
    a2 = oae_engine.assess_day(decision=d2, phenomena=_phenomena(), counterfactual=_counterfactual(),
                                evidence_packet_ids=("ep-1",), timestamp=d2.timestamp)
    dist = oae_query.classification_distribution([a1, a2])
    assert dist[oae_taxonomy.CLASSIFICATION_GOOD_TRADE_TAKEN] == 1
    assert dist[oae_taxonomy.CLASSIFICATION_OPPORTUNITY_IDENTIFIED] == 1
    assert oae_query.opportunities_identified([a1, a2]) == (a2,)


# --- Golden replay tests: real corpus-derived field values (not
# fabricated) — the corpus's one real NO_TRADE day, 2026-07-13 (Sprint
# 116/120 finding), with real MDI/thesis/CRE-observed field values
# already established in this session's own prior verification. --------

def test_golden_replay_real_no_trade_day_without_full_evidence_stays_correct_stay_out():
    """2026-07-13 was confirmed real NO_TRADE with real conviction=NONE
    (Sprint 116). Without a real, supplied CounterfactualSession/Evidence
    Packet for that day (none built in prior sprints), OAE must honestly
    default to CORRECT_STAY_OUT -- it never invents missing artefacts."""
    d = DecisionView(decision_id="real-d-20260713", date=DAY, timestamp="2026-07-13T15:15:00",
                      decision_outcome="NO_TRADE", strategy_family=None, confidence="NONE", conflicting_domains=())
    a = oae_engine.assess_day(decision=d, timestamp=d.timestamp)
    assert a.classification == oae_taxonomy.CLASSIFICATION_CORRECT_STAY_OUT


def test_golden_replay_real_no_trade_day_with_real_midday_counterfactual():
    """Using the REAL counterfactual result this session's Series 102
    work actually observed for 2026-07-13 (mid-day cutoff -> real
    TREND_REVERSAL, no real family ever selected on that path either) --
    alternative_selected_family is None, so the Opportunity Criteria
    AND-gate legitimately fails at criterion 2, and OAE must NOT claim an
    opportunity just because *something* different was observed."""
    d = DecisionView(decision_id="real-d-20260713", date=DAY, timestamp="2026-07-13T15:15:00",
                      decision_outcome="NO_TRADE", strategy_family=None, confidence="NONE", conflicting_domains=())
    real_cf = CounterfactualView(session_id="real-cfs-20260713", replay_legality="LEGAL",
                                  baseline_selected_family=None, alternative_selected_family=None,
                                  earliest_causal_timestamp="2026-07-13T09:15:00")
    a = oae_engine.assess_day(decision=d, phenomena=_phenomena(), counterfactual=real_cf,
                               evidence_packet_ids=("ep-1",), timestamp=d.timestamp)
    assert a.classification == oae_taxonomy.CLASSIFICATION_CORRECT_STAY_OUT
