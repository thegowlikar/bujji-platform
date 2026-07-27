"""Tests for Strategy Eligibility Intelligence (SEI) — BUJJI
Engineering Series 82.

STEP 0 FINDING (disclosed here and in docs/MSI_STRATEGY_ELIGIBILITY.md):
Series 80 ("Volatility Structure") does not exist on disk anywhere in
this repository -- reconfirmed by `ls bujji/ | grep -i volatility`
(only an unrelated, pre-existing `bujji/intelligence/volatility_brain.py`
exists, a different, legacy module unconnected to the MSI series). The
Deliverable 10 end-to-end demonstration in this file therefore uses a
clearly named, clearly commented `MOCK_VOLATILITY_DOMAIN_VIEW`/
`MOCK_VOLATILITY_DSE_SIGNAL` standing in for a future, not-yet-built
real brain -- never imported as if it were real, mirroring Series 81's
own established disclosed-mock pattern exactly.
"""
from __future__ import annotations

import ast
import glob
import os

from bujji.msi_decision_synthesis.models import DomainSignal, Explanation as DseExplanation, MarketOpportunityAssessment
from bujji.msi_decision_synthesis import engine as dse_engine
from bujji.msi_decision_synthesis import taxonomy as dse_taxonomy

from bujji.msi_consensus.engine import DomainAssessmentView
from bujji.msi_consensus.models import ConsensusAssessment, Explanation as MdciExplanation
from bujji.msi_consensus import engine as mdci_engine
from bujji.msi_consensus import taxonomy as mdci_taxonomy

from bujji.live_market_events import engine as lme_engine
from bujji.market_episode import engine as mee_engine
from bujji.market_observation import engine as moc_engine
from bujji.market_observation import taxonomy as moc_taxonomy

from bujji.msi_price_structure import runner as psi_runner
from bujji.msi_price_structure import taxonomy as psi_taxonomy
from bujji.msi_price_structure import serialization as psi_serialization

from bujji.msi_market_structure import runner as mssi_runner
from bujji.msi_market_structure import taxonomy as mssi_taxonomy
from bujji.msi_market_structure import serialization as mssi_serialization

from bujji.msi_strategy_eligibility import config as sei_config
from bujji.msi_strategy_eligibility import engine as sei_engine
from bujji.msi_strategy_eligibility import journal as sei_journal
from bujji.msi_strategy_eligibility import query as sei_query
from bujji.msi_strategy_eligibility import runner as sei_runner
from bujji.msi_strategy_eligibility import serialization as sei_serialization
from bujji.msi_strategy_eligibility import taxonomy as sei_taxonomy


# ---------------------------------------------------------------------------
# Construction helpers -- build real, minimal, valid MarketOpportunityAssessment
# / ConsensusAssessment objects directly (both are plain frozen dataclasses;
# no adapter needed to construct test fixtures, only to construct these
# from raw domain-signal SEQUENCES -- see the Deliverable 10 section below).
# ---------------------------------------------------------------------------
def _mk_opportunity(
    *,
    opportunity_state=dse_taxonomy.OPPORTUNITY_STATE_DIRECTIONAL,
    confidence_level=dse_taxonomy.CONFIDENCE_HIGH,
    opportunity_quality=dse_taxonomy.QUALITY_GOOD,
    assessment_id="dse-1",
) -> MarketOpportunityAssessment:
    return MarketOpportunityAssessment(
        assessment_id=assessment_id,
        timestamp="2026-07-25T09:00:00",
        opportunity_state=opportunity_state,
        confidence_level=confidence_level,
        opportunity_quality=opportunity_quality,
        supporting_domains=(dse_taxonomy.DOMAIN_PRICE_STRUCTURE, dse_taxonomy.DOMAIN_OPTIONS_MARKET_STRUCTURE),
        conflicting_domains=(),
        compatible_strategy_families=(dse_taxonomy.STRATEGY_FAMILY_DEFINED_RISK_DIRECTIONAL,),
        incompatible_strategy_families=(dse_taxonomy.STRATEGY_FAMILY_DEFINED_RISK_NEUTRAL,),
        evidence_ids=("E1", "E2"),
        episode_ids=("EP1",),
        provenance="msi_decision_synthesis.engine.synthesize",
        schema_version=dse_taxonomy.RECOGNIZED_SCHEMA_VERSIONS[0],
    )


def _mk_consensus(
    *,
    consensus_level=mdci_taxonomy.CONSENSUS_STRONG,
    evidence_sufficiency=mdci_taxonomy.SUFFICIENCY_ADEQUATE,
    conflicting_domains=(),
    missing_domains=(),
    assessment_id="mdci-1",
) -> ConsensusAssessment:
    explanation = MdciExplanation(
        assessment_id=assessment_id,
        which_domains_agree=("PRICE_STRUCTURE", "OPTIONS_MARKET_STRUCTURE"),
        which_domains_disagree=tuple(conflicting_domains),
        which_evidence_is_missing=tuple(missing_domains),
        why_consensus_is_high_or_low="deterministic test fixture",
        what_additional_domains_would_increase_confidence=(),
        what_changed=None,
        schema_version=mdci_taxonomy.MSI_CONSENSUS_VERSION,
    )
    return ConsensusAssessment(
        assessment_id=assessment_id,
        timestamp="2026-07-25T09:00:00",
        participating_domains=("PRICE_STRUCTURE", "OPTIONS_MARKET_STRUCTURE"),
        agreeing_domains=("PRICE_STRUCTURE", "OPTIONS_MARKET_STRUCTURE") if not conflicting_domains else ("PRICE_STRUCTURE",),
        conflicting_domains=tuple(conflicting_domains),
        missing_domains=tuple(missing_domains),
        consensus_level=consensus_level,
        evidence_sufficiency=evidence_sufficiency,
        contradiction_density=0.0 if not conflicting_domains else 0.5,
        confidence_calibration=mdci_taxonomy.CALIBRATION_WELL_CALIBRATED if hasattr(mdci_taxonomy, "CALIBRATION_WELL_CALIBRATED") else "UNKNOWN",
        supporting_assessment_ids=("psi-1", "mssi-1"),
        explanation=explanation,
        provenance="msi_consensus.engine.compute_consensus",
        schema_version=mdci_taxonomy.MSI_CONSENSUS_VERSION,
    )


# ---------------------------------------------------------------------------
# Determinism / assessment_id.
# ---------------------------------------------------------------------------
def test_assessment_id_deterministic_same_input_same_id():
    opportunity = _mk_opportunity()
    consensus = _mk_consensus()
    a1 = sei_engine.determine_eligibility(opportunity, consensus, timestamp="2026-07-25T09:00:00")
    a2 = sei_engine.determine_eligibility(opportunity, consensus, timestamp="2026-07-25T23:59:59")
    assert a1.assessment_id == a2.assessment_id
    assert a1.eligible_strategy_families == a2.eligible_strategy_families
    assert a1.ineligible_strategy_families == a2.ineligible_strategy_families
    assert a1.eligibility_confidence == a2.eligibility_confidence


def test_assessment_id_changes_when_inputs_change():
    opportunity = _mk_opportunity()
    consensus_strong = _mk_consensus(consensus_level=mdci_taxonomy.CONSENSUS_STRONG)
    consensus_weak = _mk_consensus(consensus_level=mdci_taxonomy.CONSENSUS_WEAK, assessment_id="mdci-2")
    a1 = sei_engine.determine_eligibility(opportunity, consensus_strong, timestamp="2026-07-25T09:00:00")
    a2 = sei_engine.determine_eligibility(opportunity, consensus_weak, timestamp="2026-07-25T09:00:00")
    assert a1.assessment_id != a2.assessment_id


# ---------------------------------------------------------------------------
# Evidence lineage.
# ---------------------------------------------------------------------------
def test_supporting_assessment_ids_reference_real_inputs():
    opportunity = _mk_opportunity(assessment_id="dse-xyz")
    consensus = _mk_consensus(assessment_id="mdci-xyz")
    a = sei_engine.determine_eligibility(opportunity, consensus, timestamp="2026-07-25T09:00:00")
    assert set(a.supporting_assessment_ids) == {"dse-xyz", "mdci-xyz"}


# ---------------------------------------------------------------------------
# Contradiction preservation: confident opportunity vs. weak consensus.
# ---------------------------------------------------------------------------
def test_contradiction_confident_opportunity_weak_consensus():
    opportunity = _mk_opportunity(confidence_level=dse_taxonomy.CONFIDENCE_HIGH)
    consensus = _mk_consensus(consensus_level=mdci_taxonomy.CONSENSUS_WEAK, evidence_sufficiency=mdci_taxonomy.SUFFICIENCY_ADEQUATE)
    a = sei_engine.determine_eligibility(opportunity, consensus, timestamp="2026-07-25T09:00:00")
    assert len(a.contradictions) >= 1
    reasons = " ".join(c.reason for c in a.contradictions)
    assert "HIGH" in reasons and "WEAK_CONSENSUS" in reasons


def test_contradiction_conflicting_domains_with_high_confidence():
    opportunity = _mk_opportunity(confidence_level=dse_taxonomy.CONFIDENCE_HIGH)
    consensus = _mk_consensus(consensus_level=mdci_taxonomy.CONSENSUS_STRONG, conflicting_domains=("OPTIONS_MARKET_STRUCTURE",))
    a = sei_engine.determine_eligibility(opportunity, consensus, timestamp="2026-07-25T09:00:00")
    assert any("CONSENSUS_CONFLICTING_DOMAINS" == c.dimension_b for c in a.contradictions)


def test_no_contradiction_when_opportunity_and_consensus_agree():
    opportunity = _mk_opportunity(confidence_level=dse_taxonomy.CONFIDENCE_HIGH)
    consensus = _mk_consensus(consensus_level=mdci_taxonomy.CONSENSUS_UNANIMOUS, evidence_sufficiency=mdci_taxonomy.SUFFICIENCY_ROBUST)
    a = sei_engine.determine_eligibility(opportunity, consensus, timestamp="2026-07-25T09:00:00")
    assert a.contradictions == ()


# ---------------------------------------------------------------------------
# THE strongest proof of Check 2's resolution: low-coherence overrides
# a confident opportunity read. A HIGH-confidence DIRECTIONAL_OPPORTUNITY
# with WEAK_CONSENSUS/INSUFFICIENT evidence must NOT produce a
# confidently-eligible family set -- something Series 77 alone, with no
# ConsensusAssessment input at all, structurally cannot enforce.
# ---------------------------------------------------------------------------
def test_low_coherence_overrides_confident_opportunity():
    opportunity = _mk_opportunity(
        opportunity_state=dse_taxonomy.OPPORTUNITY_STATE_DIRECTIONAL,
        confidence_level=dse_taxonomy.CONFIDENCE_HIGH,
    )
    consensus = _mk_consensus(
        consensus_level=mdci_taxonomy.CONSENSUS_WEAK,
        evidence_sufficiency=mdci_taxonomy.SUFFICIENCY_INSUFFICIENT,
    )
    a = sei_engine.determine_eligibility(opportunity, consensus, timestamp="2026-07-25T09:00:00")
    # INSUFFICIENT sufficiency (rank 0) collapses eligibility entirely.
    assert a.eligible_strategy_families == ()
    assert a.eligibility_confidence == sei_taxonomy.ELIGIBILITY_CONFIDENCE_NONE
    assert len(a.contradictions) >= 1


def test_reduced_coherence_caps_confidence_and_family_set():
    opportunity = _mk_opportunity(
        opportunity_state=dse_taxonomy.OPPORTUNITY_STATE_NEUTRAL,
        confidence_level=dse_taxonomy.CONFIDENCE_HIGH,
    )
    consensus = _mk_consensus(
        consensus_level=mdci_taxonomy.CONSENSUS_WEAK,       # rank 1: >= ANY (1) but < NORMAL (3)
        evidence_sufficiency=mdci_taxonomy.SUFFICIENCY_LIMITED,  # rank 1: >= ANY (1) but < NORMAL (2)
    )
    a = sei_engine.determine_eligibility(opportunity, consensus, timestamp="2026-07-25T09:00:00")
    assert a.eligibility_confidence == sei_taxonomy.ELIGIBILITY_CONFIDENCE_LOW
    # Only the conservative fallback family (DEFINED_RISK_NEUTRAL) survives,
    # not the full NEUTRAL base-eligible set (which also includes
    # UNDEFINED_RISK_PREMIUM/CALENDAR at NORMAL coherence).
    assert a.eligible_strategy_families == (sei_taxonomy.FAMILY_DEFINED_RISK_NEUTRAL,)


def test_normal_coherence_yields_full_base_eligible_set_and_high_confidence():
    opportunity = _mk_opportunity(
        opportunity_state=dse_taxonomy.OPPORTUNITY_STATE_NEUTRAL,
        confidence_level=dse_taxonomy.CONFIDENCE_HIGH,
    )
    consensus = _mk_consensus(
        consensus_level=mdci_taxonomy.CONSENSUS_UNANIMOUS,
        evidence_sufficiency=mdci_taxonomy.SUFFICIENCY_ROBUST,
    )
    a = sei_engine.determine_eligibility(opportunity, consensus, timestamp="2026-07-25T09:00:00")
    assert set(a.eligible_strategy_families) == {
        sei_taxonomy.FAMILY_DEFINED_RISK_NEUTRAL,
        sei_taxonomy.FAMILY_UNDEFINED_RISK_PREMIUM,
        sei_taxonomy.FAMILY_CALENDAR,
    }
    assert sei_taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL in a.ineligible_strategy_families
    assert a.eligibility_confidence == sei_taxonomy.ELIGIBILITY_CONFIDENCE_HIGH


# ---------------------------------------------------------------------------
# Parity: batch vs. incremental (framing resolved in runner.py's module
# docstring -- meaningful parity here is "same pair fed via both
# entrypoints => identical result", not sequence-threading parity).
# ---------------------------------------------------------------------------
def test_batch_vs_incremental_parity():
    pairs = (
        (_mk_opportunity(assessment_id="dse-a"), _mk_consensus(assessment_id="mdci-a")),
        (_mk_opportunity(assessment_id="dse-b", opportunity_state=dse_taxonomy.OPPORTUNITY_STATE_NEUTRAL), _mk_consensus(assessment_id="mdci-b")),
    )
    timestamps = ("2026-07-25T09:00:00", "2026-07-25T10:00:00")

    batch_results = sei_runner.determine_eligibility_for_cycles(pairs, timestamps=timestamps)

    stream = sei_runner.StrategyEligibilityStream()
    incremental_results = tuple(
        stream.handle_pair(opp, cons, timestamp=ts) for (opp, cons), ts in zip(pairs, timestamps)
    )

    assert len(batch_results) == len(incremental_results) == 2
    for b, i in zip(batch_results, incremental_results):
        assert b.assessment_id == i.assessment_id
        assert b.eligible_strategy_families == i.eligible_strategy_families
        assert b.ineligible_strategy_families == i.ineligible_strategy_families
        assert b.eligibility_confidence == i.eligibility_confidence


def test_same_pair_fed_twice_is_byte_identical_regardless_of_entrypoint():
    opportunity = _mk_opportunity()
    consensus = _mk_consensus()
    direct = sei_engine.determine_eligibility(opportunity, consensus, timestamp="2026-07-25T09:00:00")
    via_stream = sei_runner.StrategyEligibilityStream().handle_pair(opportunity, consensus, timestamp="2026-07-25T11:00:00")
    assert direct.assessment_id == via_stream.assessment_id
    assert direct.eligible_strategy_families == via_stream.eligible_strategy_families
    assert direct.ineligible_strategy_families == via_stream.ineligible_strategy_families


# ---------------------------------------------------------------------------
# Journal / query / serialization round trip.
# ---------------------------------------------------------------------------
def test_journal_and_query_round_trip(tmp_path):
    opportunity = _mk_opportunity()
    consensus = _mk_consensus()
    assessment = sei_engine.determine_eligibility(opportunity, consensus, timestamp="2026-07-25T09:00:00")

    j = sei_journal.StrategyEligibilityJournal(tmp_path / "sei_journal.jsonl")
    j.record_assessment(assessment)
    recovered = j.read_assessments()
    assert recovered == [assessment]

    assert sei_query.assessment_by_id((assessment,), assessment.assessment_id) == assessment
    assert assessment in sei_query.assessments_by_confidence((assessment,), assessment.eligibility_confidence)
    assert assessment in sei_query.assessments_in_time_range((assessment,), "2026-07-25T08:00:00", "2026-07-25T10:00:00")

    as_json = sei_serialization.assessment_to_json(assessment)
    round_tripped = sei_serialization.assessment_from_json(as_json)
    assert round_tripped == assessment


# ---------------------------------------------------------------------------
# Deliverable 10 -- full five-stage, five-way end-to-end pipeline
# demonstration, run twice, asserting byte-identical outputs at every
# stage. 78 -> 79 -> [mock volatility] -> 81 (Consensus), the SAME
# 78/79/mock -> 77 (Decision Synthesis) via 81's own established
# test-only DomainSignal adapter pattern, and finally 77 + 81's real,
# UNMODIFIED outputs -> 82 (Strategy Eligibility), with NO adapter.
#
# The pipeline-building code from `_mk_observation` through
# `_MSSI_TO_MDCI_LEAN`/`_mssi_to_dse_state` below is copied verbatim
# from `tests/test_msi_consensus_intelligence.py`'s own established
# Deliverable 9 fixture-building code (same real Series 73A-79 engines,
# same disclosed mock volatility stand-in) -- reused rather than
# reinvented, since it already proves 78/79/mock -> 81 and 78/79/mock
# -> 77 byte-identical-across-two-runs. This file's OWN new work is the
# final step: feeding 77's and 81's real, unmodified outputs directly
# into 82, with no adapter.
# ---------------------------------------------------------------------------
def _mk_observation(timestamp, price, obs_type=None):
    obs_type = obs_type or moc_taxonomy.ALL_OBSERVATION_TYPES[0]
    return moc_engine.build_observation(
        observation_type=obs_type, instrument="NIFTY", exchange="NSE", segment="EQ",
        timestamp=timestamp, resolution=moc_taxonomy.RESOLUTION_ONE_MINUTE, source="TEST_SOURCE",
        schema_version="1.0.0", value_kind=moc_taxonomy.VALUE_KIND_SCALAR, payload=price,
        completeness=1.0, freshness=0.0, confidence=1.0, missing_fields=(),
        validation_status=moc_taxonomy.VALIDATION_VALID, source_quality="HIGH",
        originating_source="TEST_SOURCE", acquisition_timestamp=timestamp,
        normalization_timestamp=timestamp, origin=moc_taxonomy.ORIGIN_LIVE, provenance_version="1.0.0",
    )


def _build_price_walk(prices, start="2026-07-24T09:15:00"):
    from datetime import datetime, timedelta

    obs_type = moc_taxonomy.ALL_OBSERVATION_TYPES[0]
    timestamps = [
        (datetime.fromisoformat(start) + timedelta(minutes=i)).isoformat() for i in range(len(prices))
    ]
    observations = [_mk_observation(ts, p, obs_type) for ts, p in zip(timestamps, prices)]

    events = []
    previous = None
    for obs in observations:
        for ev in lme_engine.detect_price_change(obs, previous):
            events.append(ev)
        previous = obs

    episodes = ()
    episode_snapshots = []
    event_timestamps = []
    for ev in events:
        episodes = mee_engine.advance_time(episodes, ev.timestamp, detection_context="REPLAY")
        episodes = mee_engine.process_event(episodes, ev, detection_context="REPLAY")
        episode_snapshots.append(episodes)
        event_timestamps.append(ev.timestamp)
    return episode_snapshots, tuple(events), tuple(event_timestamps)


_BREAKOUT_THEN_FAILED_RETEST_PRICES = [100, 105, 100, 110, 105, 115, 120, 125, 110, 105]

_PSI_TO_DSE_STATE = {
    psi_taxonomy.STRUCTURE_TRENDING: "Trending",
    psi_taxonomy.STRUCTURE_BALANCE: "Balanced",
    psi_taxonomy.STRUCTURE_CORRECTING: "Range",
    psi_taxonomy.STRUCTURE_TRANSITIONING: "Neutral",
    psi_taxonomy.STRUCTURE_UNKNOWN: "Neutral",
}
_CONF_TO_FLOAT = {"NONE": 0.0, "LOW": 0.3, "MODERATE": 0.6, "HIGH": 0.9}

_PSI_TO_MDCI_LEAN = {
    psi_taxonomy.STRUCTURE_TRENDING: mdci_taxonomy.LEAN_BULLISH,
    psi_taxonomy.STRUCTURE_BALANCE: mdci_taxonomy.LEAN_NEUTRAL,
    psi_taxonomy.STRUCTURE_CORRECTING: mdci_taxonomy.LEAN_NEUTRAL,
    psi_taxonomy.STRUCTURE_TRANSITIONING: mdci_taxonomy.LEAN_AMBIGUOUS,
    psi_taxonomy.STRUCTURE_UNKNOWN: mdci_taxonomy.LEAN_AMBIGUOUS,
}

_MSSI_TO_MDCI_LEAN = {
    mssi_taxonomy.LOCATION_ABOVE_RESISTANCE: mdci_taxonomy.LEAN_BULLISH,
    mssi_taxonomy.LOCATION_BELOW_SUPPORT: mdci_taxonomy.LEAN_BEARISH,
    mssi_taxonomy.LOCATION_INSIDE_RANGE: mdci_taxonomy.LEAN_NEUTRAL,
    mssi_taxonomy.LOCATION_NEAR_SUPPORT: mdci_taxonomy.LEAN_NEUTRAL,
    mssi_taxonomy.LOCATION_NEAR_RESISTANCE: mdci_taxonomy.LEAN_NEUTRAL,
    mssi_taxonomy.LOCATION_AT_RETEST: mdci_taxonomy.LEAN_AMBIGUOUS,
    mssi_taxonomy.LOCATION_UNKNOWN: mdci_taxonomy.LEAN_AMBIGUOUS,
}


def _mssi_to_dse_state(structure_location: str) -> str:
    return {
        mssi_taxonomy.LOCATION_ABOVE_RESISTANCE: "Breakout",
        mssi_taxonomy.LOCATION_BELOW_SUPPORT: "Breakdown",
        mssi_taxonomy.LOCATION_INSIDE_RANGE: "Balanced",
        mssi_taxonomy.LOCATION_NEAR_SUPPORT: "Range",
        mssi_taxonomy.LOCATION_NEAR_RESISTANCE: "Range",
        mssi_taxonomy.LOCATION_AT_RETEST: "Neutral",
        mssi_taxonomy.LOCATION_UNKNOWN: "Neutral",
    }[structure_location]


def _run_five_stage_pipeline(prices):
    snapshots, events, timestamps = _build_price_walk(prices)

    psi_assessments = psi_runner.assess_price_structure_for_episodes(tuple(snapshots), events, timestamps=timestamps)
    mssi_assessments = mssi_runner.assess_market_structure_for_episodes(tuple(snapshots), events, timestamps=timestamps)

    psi_final = psi_assessments[-1]
    mssi_final = mssi_assessments[-1]

    # Disclosed mock stand-in for the not-yet-built Series 80 brain.
    MOCK_VOLATILITY_DOMAIN_VIEW = DomainAssessmentView(
        domain_name=dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE,
        lean=mdci_taxonomy.LEAN_BULLISH,
        confidence=0.75,
        evidence_ids=("MOCK-VOL-EVID-1", "MOCK-VOL-EVID-2"),
        source_assessment_id=None,
    )
    MOCK_VOLATILITY_DSE_SIGNAL = DomainSignal(
        domain_name=dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE, state="Expanding", confidence=0.75,
        evidence_ids=("MOCK-VOL-EVID-1", "MOCK-VOL-EVID-2"),
    )

    price_structure_view = DomainAssessmentView(
        domain_name=dse_taxonomy.DOMAIN_PRICE_STRUCTURE,
        lean=_PSI_TO_MDCI_LEAN[psi_final.structure_state],
        confidence=_CONF_TO_FLOAT[psi_final.confidence],
        evidence_ids=psi_final.supporting_observation_ids,
        source_assessment_id=psi_final.assessment_id,
    )
    support_resistance_view = DomainAssessmentView(
        domain_name=dse_taxonomy.DOMAIN_SUPPORT_RESISTANCE,
        lean=_MSSI_TO_MDCI_LEAN[mssi_final.structure_location],
        confidence=_CONF_TO_FLOAT[mssi_final.confidence],
        evidence_ids=mssi_final.supporting_observation_ids,
        source_assessment_id=mssi_final.assessment_id,
    )

    consensus = mdci_engine.compute_consensus_with_explanation(
        (price_structure_view, support_resistance_view, MOCK_VOLATILITY_DOMAIN_VIEW),
        None,
        timestamp="2026-07-24T09:30:00",
    )

    price_structure_signal = DomainSignal(
        domain_name=dse_taxonomy.DOMAIN_PRICE_STRUCTURE,
        state=_PSI_TO_DSE_STATE[psi_final.structure_state],
        confidence=_CONF_TO_FLOAT[psi_final.confidence],
        evidence_ids=psi_final.supporting_observation_ids,
    )
    support_resistance_signal = DomainSignal(
        domain_name=dse_taxonomy.DOMAIN_SUPPORT_RESISTANCE,
        state=_mssi_to_dse_state(mssi_final.structure_location),
        confidence=_CONF_TO_FLOAT[mssi_final.confidence],
        evidence_ids=mssi_final.supporting_observation_ids,
    )

    opportunity = dse_engine.synthesize(
        (price_structure_signal, support_resistance_signal, MOCK_VOLATILITY_DSE_SIGNAL),
        previous_assessment=None,
        episode_ids=tuple(sorted(set(psi_final.supporting_episode_ids) | set(mssi_final.supporting_episode_ids))),
        timestamp="2026-07-24T09:30:00",
    )

    # --- Series 82 (Strategy Eligibility) -- real, unmodified 77 + 81
    # outputs consumed DIRECTLY, no adapter at all (Check 3's resolution).
    eligibility = sei_engine.determine_eligibility(
        opportunity, consensus, timestamp="2026-07-24T09:30:00",
    )

    return psi_final, mssi_final, consensus, opportunity, eligibility


def test_deliverable_10_five_stage_pipeline_byte_identical_twice():
    prices = _BREAKOUT_THEN_FAILED_RETEST_PRICES

    psi1, mssi1, consensus1, opp1, eligibility1 = _run_five_stage_pipeline(prices)
    psi2, mssi2, consensus2, opp2, eligibility2 = _run_five_stage_pipeline(prices)

    # All FIVE outputs byte-identical across the two independent runs.
    assert psi1.assessment_id == psi2.assessment_id
    assert psi_serialization.assessment_to_dict(psi1) == psi_serialization.assessment_to_dict(psi2)

    assert mssi1.assessment_id == mssi2.assessment_id
    assert mssi_serialization.assessment_to_dict(mssi1) == mssi_serialization.assessment_to_dict(mssi2)

    assert consensus1.assessment_id == consensus2.assessment_id
    assert consensus1 == consensus2

    assert opp1.assessment_id == opp2.assessment_id
    assert opp1 == opp2

    assert eligibility1.assessment_id == eligibility2.assessment_id
    assert eligibility1 == eligibility2

    # Sanity: eligibility is genuinely populated and derived from real
    # 77+81 outputs, not a placeholder.
    assert set(eligibility1.supporting_assessment_ids) == {opp1.assessment_id, consensus1.assessment_id}
    assert eligibility1.eligibility_confidence in sei_taxonomy.ALL_ELIGIBILITY_CONFIDENCE_LEVELS

    print("\n--- Deliverable 10 real measured output ---")
    print("psi.assessment_id  ==", psi1.assessment_id)
    print("mssi.assessment_id ==", mssi1.assessment_id)
    print("consensus.assessment_id ==", consensus1.assessment_id)
    print("consensus.consensus_level ==", consensus1.consensus_level)
    print("consensus.evidence_sufficiency ==", consensus1.evidence_sufficiency)
    print("opportunity.assessment_id ==", opp1.assessment_id)
    print("opportunity.opportunity_state ==", opp1.opportunity_state)
    print("opportunity.confidence_level ==", opp1.confidence_level)
    print("eligibility.assessment_id ==", eligibility1.assessment_id)
    print("eligibility.eligible_strategy_families ==", eligibility1.eligible_strategy_families)
    print("eligibility.ineligible_strategy_families ==", eligibility1.ineligible_strategy_families)
    print("eligibility.eligibility_confidence ==", eligibility1.eligibility_confidence)
    print("eligibility.contradictions ==", eligibility1.contradictions)
    print("run1 == run2 (all five) -> True")


# ---------------------------------------------------------------------------
# AST isolation -- deliberately asymmetric allow-list vs. every prior
# brain: SEI is ALLOWED to import bujji.msi_decision_synthesis and
# bujji.msi_consensus (a downstream-consumption relationship, per
# Check 3's resolution), but is still forbidden from every peer/legacy
# module every prior brain was forbidden from, PLUS bujji.
# msi_price_structure/bujji.msi_market_structure directly (Check 3's
# scoping decision -- those are two levels upstream and already
# summarized by 77/81's outputs).
# ---------------------------------------------------------------------------
_FORBIDDEN_MODULE_PREFIXES = (
    "mic_v2",
    "bujji.mic_replay",
    "bujji.production_runtime",
    "bujji.trading_brain",
    "bujji.strategy_selector",
    "fyers_apiv3",
    "bujji.msi_price_structure",
    "bujji.msi_market_structure",
)

# Explicitly and deliberately NOT forbidden (unlike every prior brain in
# this arc) -- SEI's real, documented, one-directional downstream
# dependency on Series 77 and Series 81's public output types:
_ALLOWED_DOWNSTREAM_PREFIXES = (
    "bujji.msi_decision_synthesis",
    "bujji.msi_consensus",
)

_FORBIDDEN_TRADING_TERMS = (
    "strike_price",
    "strike price",
    "probability_of_profit",
    "probability of profit",
    "pop_score",
    "direction_prediction",
    "predict_direction",
    "predicted_direction",
    "optimize_for",
    "optimization_target",
)


def _sei_source_files():
    import bujji.msi_strategy_eligibility as pkg
    package_dir = os.path.dirname(pkg.__file__)
    return sorted(glob.glob(os.path.join(package_dir, "*.py")))


def test_ast_isolation_no_forbidden_imports():
    for path in _sei_source_files():
        with open(path, "r") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in _FORBIDDEN_MODULE_PREFIXES:
                        assert not alias.name.startswith(forbidden), f"{path} imports forbidden module {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for forbidden in _FORBIDDEN_MODULE_PREFIXES:
                    assert not mod.startswith(forbidden), f"{path} imports from forbidden module {mod}"


def test_ast_isolation_allows_downstream_dse_and_consensus_imports():
    # Confirm engine.py DOES import the two allowed downstream packages
    # (proves the isolation test above is distinguishing "downstream
    # consumption" from "sibling duplication," not merely omitting a
    # check).
    engine_path = os.path.join(os.path.dirname(__import__("bujji.msi_strategy_eligibility", fromlist=["x"]).__file__), "engine.py")
    with open(engine_path, "r") as fh:
        source = fh.read()
    for allowed in _ALLOWED_DOWNSTREAM_PREFIXES:
        assert allowed in source, f"engine.py unexpectedly does not import {allowed}"


def test_ast_isolation_no_uuid4_no_unseeded_randomness_no_strategy_terms():
    for path in _sei_source_files():
        with open(path, "r") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                func_name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                assert func_name != "uuid4", f"{path} calls uuid4()"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in ("random", "uuid"), f"{path} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert mod not in ("random", "uuid"), f"{path} imports from {mod}"
        for term in _FORBIDDEN_TRADING_TERMS:
            assert term not in source, f"{path} contains forbidden term {term!r}"


def test_no_concrete_strategy_names_or_pnl_identifiers_in_package():
    forbidden_substrings = (
        "iron_condor", "iron condor", "credit_spread", "debit_spread",
        "strangle", "straddle", "butterfly", "covered_call",
        "expiry_date", "strike =", "quantity =", "lot_size", "premium_collected",
    )
    for path in _sei_source_files():
        with open(path, "r") as fh:
            source = fh.read().lower()
        for term in forbidden_substrings:
            assert term.lower() not in source, f"{path} contains forbidden concrete term {term!r}"
