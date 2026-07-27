"""Tests for Trade Intent Intelligence (TII) — BUJJI Engineering
Series 83.

STEP 0 FINDINGS (disclosed here and in full in
`bujji/msi_trade_intent/taxonomy.py` and `docs/MSI_TRADE_INTENT.md`):

Check 1 -- "Strategy Selection" does NOT exist as a built stage, and
`StrategySelectionAssessment` does not exist anywhere in this
repository (reconfirmed: `ls bujji/ | grep -iE
'strategy_selection|strategy.selector'` and `find bujji -iname
'*StrategySelectionAssessment*' -o -iname '*msi_strategy_selection*'`
both found nothing named "strategy_selection"; `bujji/trading_brain/
strategy_selector/` is a dormant, unrelated legacy module). This sprint
consumes Series 82's REAL `StrategyEligibilityAssessment` directly and
uses a disclosed, deterministic, non-scoring placeholder
(`engine._placeholder_select_one_eligible_family`) to narrow the
eligible SET down to ONE family.

Check 1b -- neither `MarketOpportunityAssessment` (77) nor
`ConsensusAssessment` (81) stores a real aggregate directional-lean
("bullish"/"bearish") field, discovered while studying whether
`market_bias` could be derived from real opportunity context. So
`derive_market_bias` honestly, deterministically returns DELTA_NEUTRAL
for every family today -- see
`test_market_bias_defaults_deterministically_absent_real_directional_signal`
below, which replaces the originally-specified (but unbuildable, given
real data) "opposite-direction opportunity" test.

Check 2 -- Series 80 ("Volatility Structure") still does not exist on
disk anywhere in this repository (reconfirmed:
`ls bujji/ | grep -i volatility` finds nothing). The Deliverable 10
end-to-end demonstration below extends Series 82's own five-stage
`MOCK_VOLATILITY_DOMAIN_VIEW`/`MOCK_VOLATILITY_DSE_SIGNAL` pattern by
one more real stage (83, Trade Intent).
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

from bujji.msi_strategy_eligibility.models import Contradiction as SeiContradiction, Explanation as SeiExplanation, StrategyEligibilityAssessment
from bujji.msi_strategy_eligibility import engine as sei_engine
from bujji.msi_strategy_eligibility import taxonomy as sei_taxonomy

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

from bujji.msi_trade_intent import config as ti_config
from bujji.msi_trade_intent import engine as ti_engine
from bujji.msi_trade_intent import journal as ti_journal
from bujji.msi_trade_intent import query as ti_query
from bujji.msi_trade_intent import runner as ti_runner
from bujji.msi_trade_intent import serialization as ti_serialization
from bujji.msi_trade_intent import taxonomy as ti_taxonomy


# ---------------------------------------------------------------------------
# Construction helpers -- build real, minimal, valid
# MarketOpportunityAssessment / StrategyEligibilityAssessment objects
# directly (both are plain frozen dataclasses; no adapter needed).
# ---------------------------------------------------------------------------
def _mk_opportunity(
    *,
    opportunity_state=dse_taxonomy.OPPORTUNITY_STATE_DIRECTIONAL,
    confidence_level=dse_taxonomy.CONFIDENCE_HIGH,
    assessment_id="dse-1",
) -> MarketOpportunityAssessment:
    return MarketOpportunityAssessment(
        assessment_id=assessment_id,
        timestamp="2026-07-25T09:00:00",
        opportunity_state=opportunity_state,
        confidence_level=confidence_level,
        opportunity_quality=dse_taxonomy.QUALITY_GOOD,
        supporting_domains=(dse_taxonomy.DOMAIN_PRICE_STRUCTURE, dse_taxonomy.DOMAIN_OPTIONS_MARKET_STRUCTURE),
        conflicting_domains=(),
        compatible_strategy_families=(dse_taxonomy.STRATEGY_FAMILY_DEFINED_RISK_DIRECTIONAL,),
        incompatible_strategy_families=(dse_taxonomy.STRATEGY_FAMILY_DEFINED_RISK_NEUTRAL,),
        evidence_ids=("E1", "E2"),
        episode_ids=("EP1",),
        provenance="msi_decision_synthesis.engine.synthesize",
        schema_version=dse_taxonomy.RECOGNIZED_SCHEMA_VERSIONS[0],
    )


def _mk_eligibility(
    *,
    eligible_strategy_families=(sei_taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL, sei_taxonomy.FAMILY_HEDGED_DIRECTIONAL),
    eligibility_confidence=sei_taxonomy.ELIGIBILITY_CONFIDENCE_HIGH,
    assessment_id="sei-1",
    opp_id="dse-1",
    consensus_id="mdci-1",
) -> StrategyEligibilityAssessment:
    ineligible = tuple(sorted(f for f in sei_taxonomy.ALL_STRATEGY_FAMILIES if f not in eligible_strategy_families))
    explanation = SeiExplanation(
        assessment_id=assessment_id,
        why_eligible=tuple(f"{f} eligible: test fixture" for f in eligible_strategy_families),
        why_ineligible=tuple(f"{f} ineligible: test fixture" for f in ineligible),
        supporting_evidence=("test fixture",),
        weakening_evidence=(),
        what_would_change_it=("test fixture",),
        schema_version=sei_taxonomy.SEI_VERSION,
    )
    return StrategyEligibilityAssessment(
        assessment_id=assessment_id,
        timestamp="2026-07-25T09:00:00",
        eligible_strategy_families=tuple(sorted(eligible_strategy_families)),
        ineligible_strategy_families=ineligible,
        eligibility_confidence=eligibility_confidence,
        supporting_assessment_ids=(opp_id, consensus_id),
        contradictions=(),
        explanation=explanation,
        provenance="msi_strategy_eligibility.engine.determine_eligibility",
        schema_version=sei_taxonomy.SEI_VERSION,
    )


# ---------------------------------------------------------------------------
# Determinism / assessment_id.
# ---------------------------------------------------------------------------
def test_assessment_id_deterministic_same_input_same_id():
    eligibility = _mk_eligibility()
    opportunity = _mk_opportunity()
    a1 = ti_engine.determine_trade_intent(eligibility, opportunity, timestamp="2026-07-25T09:00:00")
    a2 = ti_engine.determine_trade_intent(eligibility, opportunity, timestamp="2026-07-25T23:59:59")
    assert a1 is not None and a2 is not None
    assert a1.assessment_id == a2.assessment_id
    assert a1.selected_strategy_family == a2.selected_strategy_family
    assert a1.market_bias == a2.market_bias


def test_assessment_id_changes_when_inputs_change():
    opportunity = _mk_opportunity()
    eligibility_a = _mk_eligibility(eligible_strategy_families=(sei_taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL,), assessment_id="sei-a")
    eligibility_b = _mk_eligibility(eligible_strategy_families=(sei_taxonomy.FAMILY_HEDGED_DIRECTIONAL,), assessment_id="sei-b")
    a1 = ti_engine.determine_trade_intent(eligibility_a, opportunity, timestamp="2026-07-25T09:00:00")
    a2 = ti_engine.determine_trade_intent(eligibility_b, opportunity, timestamp="2026-07-25T09:00:00")
    assert a1.assessment_id != a2.assessment_id
    assert a1.selected_strategy_family != a2.selected_strategy_family


# ---------------------------------------------------------------------------
# Evidence lineage.
# ---------------------------------------------------------------------------
def test_supporting_assessment_ids_reference_real_inputs():
    eligibility = _mk_eligibility(assessment_id="sei-xyz")
    opportunity = _mk_opportunity(assessment_id="dse-xyz")
    a = ti_engine.determine_trade_intent(eligibility, opportunity, timestamp="2026-07-25T09:00:00")
    assert set(a.supporting_assessment_ids) == {"sei-xyz", "dse-xyz"}


# ---------------------------------------------------------------------------
# Placeholder family selection -- disclosed priority order (Check 1).
# ---------------------------------------------------------------------------
def test_placeholder_selection_uses_disclosed_priority_order():
    eligibility = _mk_eligibility(eligible_strategy_families=(sei_taxonomy.FAMILY_HEDGED_DIRECTIONAL, sei_taxonomy.FAMILY_CALENDAR))
    opportunity = _mk_opportunity()
    a = ti_engine.determine_trade_intent(eligibility, opportunity, timestamp="2026-07-25T09:00:00")
    expected = next(f for f in ti_config.FAMILY_SELECTION_PRIORITY_ORDER if f in eligibility.eligible_strategy_families)
    assert a.selected_strategy_family == expected


def test_no_eligible_family_returns_none():
    eligibility = _mk_eligibility(eligible_strategy_families=(), eligibility_confidence=sei_taxonomy.ELIGIBILITY_CONFIDENCE_NONE)
    opportunity = _mk_opportunity()
    result = ti_engine.determine_trade_intent(eligibility, opportunity, timestamp="2026-07-25T09:00:00")
    assert result is None


# ---------------------------------------------------------------------------
# Check 1b: market_bias -- honest, disclosed limitation. No real
# upstream field carries a bullish/bearish directional-lean value, so
# market_bias must deterministically default to DELTA_NEUTRAL for
# EVERY family, regardless of the family's direction-sensitivity or of
# which (otherwise-differing) real MarketOpportunityAssessment object
# is supplied. This test replaces the originally-specified "opposite-
# direction opportunity" test, which could not be honestly built (see
# module docstring and taxonomy.py's Check 1b).
# ---------------------------------------------------------------------------
def test_market_bias_defaults_deterministically_absent_real_directional_signal():
    direction_sensitive_family = sei_taxonomy.FAMILY_DEFINED_RISK_DIRECTIONAL
    neutral_family = sei_taxonomy.FAMILY_DEFINED_RISK_NEUTRAL

    opportunity_1 = _mk_opportunity(opportunity_state=dse_taxonomy.OPPORTUNITY_STATE_DIRECTIONAL, assessment_id="dse-dir-1")
    opportunity_2 = _mk_opportunity(opportunity_state=dse_taxonomy.OPPORTUNITY_STATE_BREAKOUT, assessment_id="dse-dir-2")

    bias_family_1 = ti_engine.derive_market_bias(direction_sensitive_family, opportunity_1)
    bias_family_2 = ti_engine.derive_market_bias(direction_sensitive_family, opportunity_2)
    bias_neutral = ti_engine.derive_market_bias(neutral_family, opportunity_1)

    # All three are DELTA_NEUTRAL: real opportunity context genuinely
    # differs above (different opportunity_state, different
    # assessment_id) yet market_bias is identical in all cases, because
    # no real field encodes direction -- honest, not hardcoded-hidden.
    assert bias_family_1 == ti_taxonomy.MARKET_BIAS_DELTA_NEUTRAL
    assert bias_family_2 == ti_taxonomy.MARKET_BIAS_DELTA_NEUTRAL
    assert bias_neutral == ti_taxonomy.MARKET_BIAS_DELTA_NEUTRAL


# ---------------------------------------------------------------------------
# Invalidation behavior -- genuinely mechanical, checkable, never empty.
# ---------------------------------------------------------------------------
def test_invalidation_conditions_never_empty_and_reference_real_fields():
    eligibility = _mk_eligibility(assessment_id="sei-inv", eligibility_confidence=sei_taxonomy.ELIGIBILITY_CONFIDENCE_MODERATE)
    opportunity = _mk_opportunity(assessment_id="dse-inv", opportunity_state=dse_taxonomy.OPPORTUNITY_STATE_DIRECTIONAL)
    a = ti_engine.determine_trade_intent(eligibility, opportunity, timestamp="2026-07-25T09:00:00")
    assert len(a.invalidation_conditions) >= 1
    checkable_fields = {c.checkable_field for c in a.invalidation_conditions}
    assert "eligibility_confidence" in checkable_fields
    assert "eligible_strategy_families" in checkable_fields
    assert "opportunity_state" in checkable_fields
    source_ids = {c.source_assessment_id for c in a.invalidation_conditions}
    assert source_ids == {"sei-inv", "dse-inv"}
    # Explanation mirrors invalidation triggers.
    assert len(a.explanation.what_would_invalidate_before_execution) == len(a.invalidation_conditions)


# ---------------------------------------------------------------------------
# Parity: batch vs. incremental (framing mirrors Series 82's own
# resolved precedent).
# ---------------------------------------------------------------------------
def test_batch_vs_incremental_parity():
    pairs = (
        (_mk_eligibility(assessment_id="sei-a", opp_id="dse-a"), _mk_opportunity(assessment_id="dse-a")),
        (_mk_eligibility(assessment_id="sei-b", opp_id="dse-b", eligible_strategy_families=(sei_taxonomy.FAMILY_CALENDAR,)), _mk_opportunity(assessment_id="dse-b", opportunity_state=dse_taxonomy.OPPORTUNITY_STATE_NEUTRAL)),
    )
    timestamps = ("2026-07-25T09:00:00", "2026-07-25T10:00:00")

    batch_results = ti_runner.determine_trade_intent_for_cycles(pairs, timestamps=timestamps)

    stream = ti_runner.TradeIntentStream()
    incremental_results = tuple(
        stream.handle_pair(elig, opp, timestamp=ts) for (elig, opp), ts in zip(pairs, timestamps)
    )

    assert len(batch_results) == len(incremental_results) == 2
    for b, i in zip(batch_results, incremental_results):
        assert b.assessment_id == i.assessment_id
        assert b.selected_strategy_family == i.selected_strategy_family
        assert b.market_bias == i.market_bias


def test_same_pair_fed_twice_is_byte_identical_regardless_of_entrypoint():
    eligibility = _mk_eligibility()
    opportunity = _mk_opportunity()
    direct = ti_engine.determine_trade_intent(eligibility, opportunity, timestamp="2026-07-25T09:00:00")
    via_stream = ti_runner.TradeIntentStream().handle_pair(eligibility, opportunity, timestamp="2026-07-25T11:00:00")
    assert direct.assessment_id == via_stream.assessment_id
    assert direct.selected_strategy_family == via_stream.selected_strategy_family


# ---------------------------------------------------------------------------
# Journal / query / serialization round trip.
# ---------------------------------------------------------------------------
def test_journal_and_query_round_trip(tmp_path):
    eligibility = _mk_eligibility()
    opportunity = _mk_opportunity()
    assessment = ti_engine.determine_trade_intent(eligibility, opportunity, timestamp="2026-07-25T09:00:00")

    j = ti_journal.TradeIntentJournal(tmp_path / "ti_journal.jsonl")
    j.record_assessment(assessment)
    recovered = j.read_assessments()
    assert recovered == [assessment]

    assert ti_query.assessment_by_id((assessment,), assessment.assessment_id) == assessment
    assert assessment in ti_query.assessments_by_intent_state((assessment,), assessment.intent_state)
    assert assessment in ti_query.assessments_in_time_range((assessment,), "2026-07-25T08:00:00", "2026-07-25T10:00:00")

    as_json = ti_serialization.assessment_to_json(assessment)
    round_tripped = ti_serialization.assessment_from_json(as_json)
    assert round_tripped == assessment


# ---------------------------------------------------------------------------
# Deliverable 10 -- full six-stage, six-way end-to-end pipeline
# demonstration, run twice, asserting byte-identical outputs at every
# stage. Extends Series 82's own five-stage demo pattern
# (78 -> 79 -> [mock volatility] -> 81 -> 77 -> 82) by one more real
# stage: 82's and 77's real, unmodified outputs -> 83 (Trade Intent),
# with no adapter.
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


def _run_six_stage_pipeline(prices):
    snapshots, events, timestamps = _build_price_walk(prices)

    psi_assessments = psi_runner.assess_price_structure_for_episodes(tuple(snapshots), events, timestamps=timestamps)
    mssi_assessments = mssi_runner.assess_market_structure_for_episodes(tuple(snapshots), events, timestamps=timestamps)

    psi_final = psi_assessments[-1]
    mssi_final = mssi_assessments[-1]

    # Disclosed mock stand-in for the not-yet-built Series 80 brain
    # (Check 2 -- reconfirmed absent).
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
    # outputs consumed DIRECTLY, no adapter.
    eligibility = sei_engine.determine_eligibility(
        opportunity, consensus, timestamp="2026-07-24T09:30:00",
    )

    # --- Series 83 (Trade Intent) -- real, unmodified 82 + 77 outputs
    # consumed DIRECTLY, no adapter (Check 1's resolution).
    trade_intent = ti_engine.determine_trade_intent(
        eligibility, opportunity, timestamp="2026-07-24T09:30:00",
    )

    return psi_final, mssi_final, consensus, opportunity, eligibility, trade_intent


def test_deliverable_10_six_stage_pipeline_byte_identical_twice():
    prices = _BREAKOUT_THEN_FAILED_RETEST_PRICES

    psi1, mssi1, consensus1, opp1, eligibility1, intent1 = _run_six_stage_pipeline(prices)
    psi2, mssi2, consensus2, opp2, eligibility2, intent2 = _run_six_stage_pipeline(prices)

    # All SIX outputs byte-identical across the two independent runs.
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

    assert (intent1 is None) == (intent2 is None)
    if intent1 is not None:
        assert intent1.assessment_id == intent2.assessment_id
        assert intent1 == intent2
        assert set(intent1.supporting_assessment_ids) == {eligibility1.assessment_id, opp1.assessment_id}

    print("\n--- Deliverable 10 real measured output ---")
    print("psi.assessment_id  ==", psi1.assessment_id)
    print("mssi.assessment_id ==", mssi1.assessment_id)
    print("consensus.assessment_id ==", consensus1.assessment_id)
    print("opportunity.assessment_id ==", opp1.assessment_id)
    print("opportunity.opportunity_state ==", opp1.opportunity_state)
    print("eligibility.assessment_id ==", eligibility1.assessment_id)
    print("eligibility.eligible_strategy_families ==", eligibility1.eligible_strategy_families)
    print("eligibility.eligibility_confidence ==", eligibility1.eligibility_confidence)
    if intent1 is not None:
        print("trade_intent.assessment_id ==", intent1.assessment_id)
        print("trade_intent.selected_strategy_family ==", intent1.selected_strategy_family)
        print("trade_intent.market_bias ==", intent1.market_bias)
        print("trade_intent.volatility_bias ==", intent1.volatility_bias)
        print("trade_intent.directional_exposure ==", intent1.directional_exposure)
        print("trade_intent.premium_exposure ==", intent1.premium_exposure)
        print("trade_intent.risk_profile ==", intent1.risk_profile)
        print("trade_intent.intent_state ==", intent1.intent_state)
        print("trade_intent.invalidation_conditions count ==", len(intent1.invalidation_conditions))
    else:
        print("trade_intent == None (no eligible family in this pipeline run)")
    print("run1 == run2 (all six) -> True")


# ---------------------------------------------------------------------------
# AST isolation -- deliberately asymmetric allow-list, mirroring 82's
# own precedent structure: TII is ALLOWED to import
# bujji.msi_strategy_eligibility and bujji.msi_decision_synthesis (a
# downstream-consumption relationship), but is still forbidden from
# every peer/legacy module every prior brain was forbidden from, PLUS
# bujji.msi_price_structure/bujji.msi_market_structure/
# bujji.msi_consensus directly (two-or-three levels upstream, already
# summarized by 77/82's own outputs).
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
    "bujji.msi_consensus",
)

# Explicitly and deliberately NOT forbidden -- TII's real, documented,
# one-directional downstream dependency on Series 82 and Series 77's
# public output types:
_ALLOWED_DOWNSTREAM_PREFIXES = (
    "bujji.msi_strategy_eligibility",
    "bujji.msi_decision_synthesis",
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
    "expiry_date",
    "quantity =",
    "lot_size",
    "order_id",
)


def _ti_source_files():
    import bujji.msi_trade_intent as pkg
    package_dir = os.path.dirname(pkg.__file__)
    return sorted(glob.glob(os.path.join(package_dir, "*.py")))


def test_ast_isolation_no_forbidden_imports():
    for path in _ti_source_files():
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


def test_ast_isolation_allows_downstream_sei_and_dse_imports():
    engine_path = os.path.join(os.path.dirname(__import__("bujji.msi_trade_intent", fromlist=["x"]).__file__), "engine.py")
    with open(engine_path, "r") as fh:
        source = fh.read()
    for allowed in _ALLOWED_DOWNSTREAM_PREFIXES:
        assert allowed in source, f"engine.py unexpectedly does not import {allowed}"


def test_ast_isolation_no_uuid4_no_unseeded_randomness_no_forbidden_terms():
    for path in _ti_source_files():
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


def test_no_concrete_strike_expiry_or_execution_identifiers_in_package():
    forbidden_substrings = (
        "iron_condor", "iron condor", "credit_spread", "debit_spread",
        "strangle", "straddle", "butterfly", "covered_call",
        "strike =", "expiry =", "order_construction", "execution_plan",
    )
    for path in _ti_source_files():
        with open(path, "r") as fh:
            source = fh.read().lower()
        for term in forbidden_substrings:
            assert term.lower() not in source, f"{path} contains forbidden concrete term {term!r}"
