"""Tests -- Domain View Adapter, Shadow Trading Brain Phase 5B.
No broker, no network -- directly constructed real assessment
dataclasses only. Reuses each package's own real assessment_id
computation via their real engine functions where feasible
(determine_market_direction) to avoid hand-faking ids."""
from __future__ import annotations

from bujji.msi_consensus import taxonomy as consensus_taxonomy
from bujji.msi_decision_synthesis import taxonomy as dse_taxonomy
from bujji.msi_market_direction.engine import determine_market_direction
from bujji.msi_market_structure.models import Explanation as MssiExplanation
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_participant_positioning.models import Explanation as MppiExplanation
from bujji.msi_participant_positioning.models import MarketParticipantPositioningAssessment
from bujji.msi_price_structure.models import Explanation as PsiExplanation
from bujji.msi_price_structure.models import PriceStructureAssessment
from bujji.msi_volatility_structure.models import Explanation as VsbExplanation
from bujji.msi_volatility_structure.models import VolatilityStructureAssessment
from bujji.market_state.domain_view_adapter import (
    MARKET_DIRECTION_DOMAIN_NAME,
    build_domain_assessment_views,
    build_domain_signals,
)
from bujji.msi_consensus.engine import compute_consensus
from bujji.msi_decision_synthesis.engine import synthesize

TS = "2026-08-03T09:15:00+05:30"


def make_psi(trend_direction_signal="UP", confidence="MODERATE"):
    return PriceStructureAssessment(
        assessment_id="PSI-1", timestamp=TS, structure_state="RANGE_BOUND",
        trend_state="ESTABLISHED_TREND", swing_state="NONE", compression_state="NONE",
        expansion_state="NONE", balance_state="BALANCED", structure_integrity="COHERENT",
        confidence=confidence, supporting_episode_ids=("EP-1",), supporting_event_ids=("MEVT-1",),
        supporting_observation_ids=("OBS-1",), contradictions=(),
        explanation=PsiExplanation(
            assessment_id="PSI-1", what_changed=None, why=(), which_episodes_caused_it=("EP-1",),
            which_observations_support_it=("OBS-1",), missing_evidence=(), would_increase_confidence=(),
            schema_version="1.0",
        ),
        provenance="test", schema_version="1.0", trend_direction_signal=trend_direction_signal,
    )


def make_mssi(structure_location="ABOVE_RESISTANCE", breakout_state="CONFIRMED", breakdown_state="NONE", confidence="HIGH"):
    return MarketStructureAssessment(
        assessment_id="MSSI-1", timestamp=TS, structure_location=structure_location,
        support_state="NONE", resistance_state="NONE", breakout_state=breakout_state,
        breakdown_state=breakdown_state, retest_state="NONE", rejection_state="NONE",
        structural_balance="BALANCED", confidence=confidence,
        supporting_episode_ids=("EP-1",), supporting_event_ids=("MEVT-1",),
        supporting_observation_ids=("OBS-1",), contradictions=(),
        explanation=MssiExplanation(
            assessment_id="MSSI-1", what_changed=None, why=(), which_episodes_caused_it=("EP-1",),
            which_observations_support_it=("OBS-1",), missing_evidence=(), would_increase_confidence=(),
            schema_version="1.0",
        ),
        provenance="test", schema_version="1.0",
    )


def make_mppi(positioning_bias="BULLISH_POSITIONING", positioning_strength="MODERATE"):
    return MarketParticipantPositioningAssessment(
        assessment_id="MPPI-1", timestamp=TS, positioning_bias=positioning_bias,
        positioning_strength=positioning_strength, participating_lenses=(), conflicting_lenses=(),
        supporting_observation_ids=("OBS-2",),
        explanation=MppiExplanation(
            assessment_id="MPPI-1", which_lenses_participated=(), which_bullish=(), which_bearish=(),
            which_neutral_or_unknown=(), per_lens_evidence=(), missing_evidence=(),
            why_positioning_was_chosen="test", schema_version="1.0",
        ),
        provenance="test", schema_version="1.0",
    )


def make_vsb(volatility_regime="NORMAL", confidence="MODERATE"):
    return VolatilityStructureAssessment(
        assessment_id="VSB-1", timestamp=TS, volatility_regime=volatility_regime,
        iv_state="NORMAL", expected_move_state="NORMAL", skew_state="UNKNOWN",
        term_structure_state="UNKNOWN", expansion_state="NONE", compression_state="NONE",
        confidence=confidence, iv_average=0.15, realized_vol=0.14, expected_move_pct=1.2,
        explanation=VsbExplanation(assessment_id="VSB-1", why=(), missing_evidence=(), would_increase_confidence=(), schema_version="1.0"),
        provenance="test", schema_version="1.0",
    )


def make_mdi(psi, mssi):
    return determine_market_direction(psi, mssi, timestamp=TS)


# --- 1. Full PSI/MSSI/MDI/MPPI/VSB input ---

def test_full_input_produces_all_domains_in_both_outputs():
    psi, mssi, mppi, vsb = make_psi(), make_mssi(), make_mppi(), make_vsb()
    mdi = make_mdi(psi, mssi)

    views = build_domain_assessment_views(psi, mssi, mdi, mppi)
    signals = build_domain_signals(psi, mssi, mdi, mppi, vsb)

    view_domains = {v.domain_name for v in views}
    signal_domains = {s.domain_name for s in signals}

    assert view_domains == {
        dse_taxonomy.DOMAIN_PRICE_STRUCTURE, dse_taxonomy.DOMAIN_SUPPORT_RESISTANCE,
        MARKET_DIRECTION_DOMAIN_NAME, dse_taxonomy.DOMAIN_OPTIONS_MARKET_STRUCTURE,
    }
    # vsb present in signals but never in views (Decision 2).
    assert signal_domains == view_domains | {dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE}


# --- 2. Missing optional VSB ---

def test_missing_vsb_omitted_from_signals_views_unaffected():
    psi, mssi, mppi = make_psi(), make_mssi(), make_mppi()
    mdi = make_mdi(psi, mssi)

    signals = build_domain_signals(psi, mssi, mdi, mppi, vsb=None)
    assert dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE not in {s.domain_name for s in signals}

    views = build_domain_assessment_views(psi, mssi, mdi, mppi)
    assert len(views) == 4  # unaffected by vsb being absent, since vsb was never a views input


# --- 3. Missing MPPI ---

def test_missing_mppi_omitted_from_both_outputs_no_crash():
    psi, mssi = make_psi(), make_mssi()
    mdi = make_mdi(psi, mssi)

    views = build_domain_assessment_views(psi, mssi, mdi, mppi=None)
    signals = build_domain_signals(psi, mssi, mdi, mppi=None, vsb=make_vsb())

    assert dse_taxonomy.DOMAIN_OPTIONS_MARKET_STRUCTURE not in {v.domain_name for v in views}
    assert dse_taxonomy.DOMAIN_OPTIONS_MARKET_STRUCTURE not in {s.domain_name for s in signals}
    assert len(views) == 3
    assert len(signals) == 4  # psi, mssi, mdi, vsb


# --- 4. UNKNOWN direction ---

def test_unknown_direction_collapses_to_ambiguous_lean():
    # No trend_direction_signal + no structure_location signal -> mdi's
    # own reconcile_lenses() genuinely produces UNKNOWN.
    psi = make_psi(trend_direction_signal=None)
    mssi = make_mssi(structure_location="INSIDE_RANGE", breakout_state="NONE", breakdown_state="NONE")
    mdi = make_mdi(psi, mssi)
    assert mdi.overall_direction == "UNKNOWN"

    views = build_domain_assessment_views(psi, mssi, mdi, mppi=None)
    mdi_view = next(v for v in views if v.domain_name == MARKET_DIRECTION_DOMAIN_NAME)
    assert mdi_view.lean == consensus_taxonomy.LEAN_AMBIGUOUS

    signals = build_domain_signals(psi, mssi, mdi, mppi=None, vsb=None)
    mdi_signal = next(s for s in signals if s.domain_name == MARKET_DIRECTION_DOMAIN_NAME)
    assert mdi_signal.state == "UNKNOWN"


# --- 5. Confidence mapping ---

def test_confidence_levels_map_to_expected_floats():
    for level, expected in (("NONE", 0.0), ("LOW", 0.33), ("MODERATE", 0.67), ("HIGH", 1.0)):
        psi = make_psi(confidence=level)
        signals = build_domain_signals(psi, None, None, None, None)
        assert signals[0].confidence == expected


def test_positioning_strength_maps_to_expected_floats_distinct_scale():
    for strength, expected in (("UNKNOWN", 0.0), ("WEAK", 0.33), ("MODERATE", 0.67), ("STRONG", 1.0)):
        mppi = make_mppi(positioning_strength=strength)
        signals = build_domain_signals(None, None, None, mppi, None)
        assert signals[0].confidence == expected


# --- 6. Evidence preservation ---

def test_evidence_ids_preserved_from_source_assessments():
    psi, mssi = make_psi(), make_mssi()
    views = build_domain_assessment_views(psi, mssi, None, None)
    psi_view = next(v for v in views if v.domain_name == dse_taxonomy.DOMAIN_PRICE_STRUCTURE)
    assert psi_view.evidence_ids == psi.supporting_observation_ids
    assert psi_view.source_assessment_id == "PSI-1"

    signals = build_domain_signals(psi, mssi, None, None, None)
    psi_signal = next(s for s in signals if s.domain_name == dse_taxonomy.DOMAIN_PRICE_STRUCTURE)
    assert psi_signal.evidence_ids == psi.supporting_observation_ids


def test_vsb_signal_evidence_ids_honestly_empty():
    # VolatilityStructureAssessment has no supporting_observation_ids
    # field at all -- confirming this is never fabricated.
    vsb = make_vsb()
    signals = build_domain_signals(None, None, None, None, vsb)
    assert signals[0].evidence_ids == ()


# --- 7. MDI non-registry behavior ---

def test_mdi_non_registry_domain_name_and_limitation():
    psi, mssi = make_psi(), make_mssi()
    mdi = make_mdi(psi, mssi)
    views = build_domain_assessment_views(psi, mssi, mdi, None)
    mdi_view = next(v for v in views if v.domain_name == MARKET_DIRECTION_DOMAIN_NAME)
    # MARKET_DIRECTION is confirmed NOT a member of the shared 9-domain
    # registry both msi_consensus and msi_decision_synthesis use --
    # this is the disclosed, approved Phase 5B Decision 1 tradeoff.
    assert MARKET_DIRECTION_DOMAIN_NAME not in dse_taxonomy.ALL_MSI_DOMAINS
    assert mdi_view.domain_name == MARKET_DIRECTION_DOMAIN_NAME


# --- Step 3: wiring into the real compute_consensus()/synthesize() ---
# Integration-level only -- confirms the adapter's real output is
# actually consumable by the real engines end to end. No new
# production wiring function is added to domain_view_adapter.py itself
# (it stays a pure translator, per Phase 5B's own constraint) -- this
# is validation, not a new responsibility.

def test_adapter_output_wires_into_compute_consensus_without_error():
    psi, mssi, mppi = make_psi(), make_mssi(), make_mppi()
    mdi = make_mdi(psi, mssi)
    views = build_domain_assessment_views(psi, mssi, mdi, mppi)

    consensus = compute_consensus(views, timestamp=TS)

    assert consensus.consensus_level in consensus_taxonomy.ALL_CONSENSUS_LEVELS
    # MARKET_DIRECTION participates in agreement accounting...
    assert MARKET_DIRECTION_DOMAIN_NAME in consensus.participating_domains
    # ...but never appears in missing_domains, since it's outside the
    # registry compute_consensus checks against (the disclosed Decision 1 tradeoff).
    assert MARKET_DIRECTION_DOMAIN_NAME not in consensus.missing_domains


def test_adapter_output_wires_into_synthesize_without_error():
    psi, mssi, mppi, vsb = make_psi(), make_mssi(), make_mppi(), make_vsb()
    mdi = make_mdi(psi, mssi)
    signals = build_domain_signals(psi, mssi, mdi, mppi, vsb)

    opportunity = synthesize(signals, None, (), timestamp=TS)

    assert opportunity.opportunity_state  # a real, non-empty computed state
    assert opportunity.assessment_id
    # vsb's evidence (honestly empty) and the other domains' real
    # evidence ids all flow through into the final union.
    assert set(opportunity.evidence_ids) >= {"OBS-1", "OBS-2"}


def test_all_none_inputs_produce_empty_outputs_no_crash():
    assert build_domain_assessment_views() == ()
    assert build_domain_signals() == ()
    consensus = compute_consensus((), timestamp=TS)
    opportunity = synthesize((), None, (), timestamp=TS)
    assert consensus is not None
    assert opportunity is not None
