"""Tests for Multi-Domain Consensus Intelligence (MDCI) — BUJJI
Engineering Series 81.

STEP 0 FINDING (disclosed here and in docs/MSI_CONSENSUS.md): Series 80
("Volatility Structure") does not exist on disk anywhere in this
repository -- confirmed by `ls bujji/ | grep -i volatility` (only an
unrelated, pre-existing `bujji/intelligence/volatility_brain.py`
exists, a different, legacy module unconnected to the MSI series). The
Deliverable 9 integration demonstration below therefore uses a clearly
named, clearly commented MOCK_VOLATILITY_DOMAIN_VIEW standing in for a
future, not-yet-built real brain -- never imported as if it were real.
"""
from __future__ import annotations

import ast
import glob
import os
import random

from bujji.live_market_events import engine as lme_engine
from bujji.market_episode import engine as mee_engine
from bujji.market_observation import engine as moc_engine
from bujji.market_observation import taxonomy as moc_taxonomy

from bujji.msi_decision_synthesis import engine as dse_engine
from bujji.msi_decision_synthesis import taxonomy as dse_taxonomy
from bujji.msi_decision_synthesis.models import DomainSignal

from bujji.msi_price_structure import runner as psi_runner
from bujji.msi_price_structure import taxonomy as psi_taxonomy
from bujji.msi_price_structure import serialization as psi_serialization

from bujji.msi_market_structure import runner as mssi_runner
from bujji.msi_market_structure import taxonomy as mssi_taxonomy
from bujji.msi_market_structure import serialization as mssi_serialization

from bujji.msi_consensus import config as mdci_config
from bujji.msi_consensus import engine as mdci_engine
from bujji.msi_consensus import journal as mdci_journal
from bujji.msi_consensus import query as mdci_query
from bujji.msi_consensus import runner as mdci_runner
from bujji.msi_consensus import serialization as mdci_serialization
from bujji.msi_consensus import taxonomy as mdci_taxonomy
from bujji.msi_consensus.engine import DomainAssessmentView


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
def _mk_views():
    return (
        DomainAssessmentView("PRICE_STRUCTURE", mdci_taxonomy.LEAN_BULLISH, 0.8, ("E1", "E2", "E3"), "psi-1"),
        DomainAssessmentView("SUPPORT_RESISTANCE", mdci_taxonomy.LEAN_BULLISH, 0.7, ("E4", "E5"), "mssi-1"),
        DomainAssessmentView("LIQUIDITY", mdci_taxonomy.LEAN_AMBIGUOUS, 0.6, ("E6",)),
    )


def test_assessment_id_deterministic_same_input_same_id():
    views = _mk_views()
    a1 = mdci_engine.compute_consensus_with_explanation(views, None, timestamp="2026-07-25T09:00:00")
    a2 = mdci_engine.compute_consensus_with_explanation(views, None, timestamp="2099-01-01T00:00:00")
    assert a1.assessment_id == a2.assessment_id
    assert a1.consensus_level == a2.consensus_level
    assert a1.evidence_sufficiency == a2.evidence_sufficiency


def test_assessment_id_changes_when_evidence_changes():
    views1 = _mk_views()
    views2 = (
        DomainAssessmentView("PRICE_STRUCTURE", mdci_taxonomy.LEAN_BEARISH, 0.8, ("E1", "E2", "E3"), "psi-1"),
        DomainAssessmentView("SUPPORT_RESISTANCE", mdci_taxonomy.LEAN_BULLISH, 0.7, ("E4", "E5"), "mssi-1"),
    )
    a1 = mdci_engine.compute_consensus(views1, timestamp="2026-07-25T09:00:00")
    a2 = mdci_engine.compute_consensus(views2, timestamp="2026-07-25T09:00:00")
    assert a1.assessment_id != a2.assessment_id


def test_assessment_id_order_independent():
    views = _mk_views()
    reordered = tuple(reversed(views))
    a1 = mdci_engine.compute_consensus(views, timestamp="2026-07-25T09:00:00")
    a2 = mdci_engine.compute_consensus(reordered, timestamp="2026-07-25T09:00:00")
    assert a1.assessment_id == a2.assessment_id


# ---------------------------------------------------------------------------
# Replay / live parity
# ---------------------------------------------------------------------------
def test_replay_live_parity():
    cycle1 = (
        DomainAssessmentView("PRICE_STRUCTURE", mdci_taxonomy.LEAN_BULLISH, 0.8, ("E1",), "psi-1"),
        DomainAssessmentView("SUPPORT_RESISTANCE", mdci_taxonomy.LEAN_NEUTRAL, 0.5, ("E2",), "mssi-1"),
    )
    cycle2 = (
        DomainAssessmentView("PRICE_STRUCTURE", mdci_taxonomy.LEAN_BULLISH, 0.85, ("E1", "E3"), "psi-2"),
        DomainAssessmentView("SUPPORT_RESISTANCE", mdci_taxonomy.LEAN_BULLISH, 0.6, ("E2", "E4"), "mssi-2"),
    )
    timestamps = ("2026-07-25T09:00:00", "2026-07-25T09:01:00")

    batch = mdci_runner.compute_consensus_for_cycles((cycle1, cycle2), timestamps=timestamps)

    stream = mdci_runner.ConsensusStream()
    live0 = stream.handle_domain_views(cycle1, timestamp=timestamps[0])
    live1 = stream.handle_domain_views(cycle2, timestamp=timestamps[1])

    # provenance legitimately differs by design (REPLAY vs. LIVE
    # entrypoint label, mirroring `bujji.msi_market_structure.runner`'s
    # own precedent) -- every OTHER field, including assessment_id and
    # the full Explanation, must be identical.
    import dataclasses
    def _without_provenance(a):
        return dataclasses.replace(a, provenance="")
    assert _without_provenance(batch[0]) == _without_provenance(live0)
    assert _without_provenance(batch[1]) == _without_provenance(live1)
    assert batch[0].assessment_id == live0.assessment_id
    assert batch[1].assessment_id == live1.assessment_id
    # what_changed must be populated on the second cycle since
    # consensus_level genuinely changes (NO_CONSENSUS/WEAK -> higher).
    assert batch[1].explanation.what_changed is not None


# ---------------------------------------------------------------------------
# Monotonic consensus scoring — a real sweep, not just two data points.
# ---------------------------------------------------------------------------
def test_consensus_level_monotonic_with_agreement_ratio():
    # Direct sweep of engine.compute_consensus_level over a real
    # agreement_ratio range 0.0 -> 1.0, mirroring the DSE precedent
    # (tests/test_msi_decision_synthesis_engine.py's monotonicity test
    # sweeps the pure banding function directly, avoiding the
    # majority-vote lean-flip/tie ambiguity a full end-to-end sweep
    # would introduce -- see docs/MSI_CONSENSUS.md for why a full
    # compute_agreement-based sweep is NOT monotonic in n_agree once
    # the majority itself flips mid-sweep).
    ratios = [None, 0.0, 0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0]
    ranks = [mdci_taxonomy.CONSENSUS_LEVEL_RANK[mdci_engine.compute_consensus_level(r)] for r in ratios]
    for i in range(1, len(ranks)):
        assert ranks[i] >= ranks[i - 1], f"rank decreased at step {i}: {ranks}"
    assert ranks[-1] > ranks[0]

    # Also confirm via the full engine on a scenario carefully
    # constructed so the SAME domain keeps winning throughout (no
    # mid-sweep lean flip): a fixed majority-agreeing domain set that
    # only grows, plus a fixed single always-conflicting domain.
    ranks_full = []
    for n_agree in range(1, 6):
        views = [DomainAssessmentView(f"AGREE_{i}", mdci_taxonomy.LEAN_BULLISH, 0.6, ("E1",)) for i in range(n_agree)]
        views.append(DomainAssessmentView("ALWAYS_CONFLICT", mdci_taxonomy.LEAN_BEARISH, 0.6, ("E2",)))
        assessment = mdci_engine.compute_consensus(tuple(views), timestamp="t")
        ranks_full.append(mdci_taxonomy.CONSENSUS_LEVEL_RANK[assessment.consensus_level])
    for i in range(1, len(ranks_full)):
        assert ranks_full[i] >= ranks_full[i - 1], f"rank decreased at step {i}: {ranks_full}"
    assert ranks_full[-1] > ranks_full[0]


def test_evidence_sufficiency_monotonic_with_coverage():
    expected = ("D1", "D2", "D3", "D4")
    ranks = []
    for n_participating in range(0, 5):
        views = tuple(
            DomainAssessmentView(f"D{i+1}", mdci_taxonomy.LEAN_NEUTRAL, 0.6, ("E1", "E2", "E3"))
            for i in range(n_participating)
        )
        level = mdci_engine.compute_evidence_sufficiency(views, expected_domains=expected)
        ranks.append(mdci_taxonomy.SUFFICIENCY_RANK[level])
    for i in range(1, len(ranks)):
        assert ranks[i] >= ranks[i - 1], f"sufficiency rank decreased at step {i}: {ranks}"
    assert ranks[-1] > ranks[0]


# ---------------------------------------------------------------------------
# Contradiction preservation — never silently resolved/dropped.
# ---------------------------------------------------------------------------
def test_contradiction_preservation_real_conflict_scenario():
    views = (
        DomainAssessmentView("PRICE_STRUCTURE", mdci_taxonomy.LEAN_BULLISH, 0.8, ("E1", "E2")),
        DomainAssessmentView("SUPPORT_RESISTANCE", mdci_taxonomy.LEAN_BEARISH, 0.75, ("E3", "E4")),
    )
    assessment = mdci_engine.compute_consensus(views, timestamp="2026-07-25T09:00:00")
    assert assessment.conflicting_domains != ()
    assert "PRICE_STRUCTURE" in assessment.conflicting_domains or "PRICE_STRUCTURE" in assessment.agreeing_domains
    assert set(assessment.agreeing_domains) | set(assessment.conflicting_domains) == {"PRICE_STRUCTURE", "SUPPORT_RESISTANCE"}
    assert assessment.consensus_level == mdci_taxonomy.CONSENSUS_NO_CONSENSUS

    contradictions = mdci_engine.detect_cross_domain_contradictions(views)
    assert len(contradictions) == 1
    c = contradictions[0]
    assert {c.dimension_a, c.dimension_b} == {"PRICE_STRUCTURE", "SUPPORT_RESISTANCE"}
    assert assessment.contradiction_density == 1.0


def test_contradiction_density_formula():
    # 3 considered domains, 1 disagrees -> density == 1/3.
    views = (
        DomainAssessmentView("A", mdci_taxonomy.LEAN_BULLISH, 0.7, ("E1",)),
        DomainAssessmentView("B", mdci_taxonomy.LEAN_BULLISH, 0.7, ("E2",)),
        DomainAssessmentView("C", mdci_taxonomy.LEAN_BEARISH, 0.7, ("E3",)),
    )
    assessment = mdci_engine.compute_consensus(views, timestamp="2026-07-25T09:00:00")
    assert assessment.contradiction_density == 1.0 / 3.0
    assert assessment.conflicting_domains == ("C",)
    assert assessment.agreeing_domains == ("A", "B")


# ---------------------------------------------------------------------------
# Evidence sufficiency / missing-domain handling.
# ---------------------------------------------------------------------------
def test_missing_domain_detection_and_evidence_sufficiency():
    expected = ("PRICE_STRUCTURE", "SUPPORT_RESISTANCE", "VOLATILITY_STRUCTURE", "LIQUIDITY")
    views = (
        DomainAssessmentView("PRICE_STRUCTURE", mdci_taxonomy.LEAN_BULLISH, 0.8, ("E1",)),
    )
    assessment = mdci_engine.compute_consensus_with_explanation(views, None, timestamp="2026-07-25T09:00:00", expected_domains=expected)
    assert assessment.missing_domains == ("LIQUIDITY", "SUPPORT_RESISTANCE", "VOLATILITY_STRUCTURE")
    # Low coverage (1/4) and thin evidence (1 id) -> low sufficiency.
    assert assessment.evidence_sufficiency in (mdci_taxonomy.SUFFICIENCY_INSUFFICIENT, mdci_taxonomy.SUFFICIENCY_LIMITED)
    assert "LIQUIDITY" in assessment.explanation.which_evidence_is_missing


def test_confidence_calibration_overconfident_and_underconfident():
    overconfident_view = DomainAssessmentView("A", mdci_taxonomy.LEAN_BULLISH, 0.95, ("E1",))
    underconfident_view = DomainAssessmentView("B", mdci_taxonomy.LEAN_BULLISH, 0.10, ("E1", "E2", "E3"))
    unjudgeable_view = DomainAssessmentView("C", mdci_taxonomy.LEAN_NEUTRAL, 0.5, ())

    a1 = mdci_engine.compute_consensus((overconfident_view,), timestamp="t")
    assert a1.confidence_calibration == mdci_taxonomy.CALIBRATION_OVERCONFIDENT

    a2 = mdci_engine.compute_consensus((underconfident_view,), timestamp="t")
    assert a2.confidence_calibration == mdci_taxonomy.CALIBRATION_UNDERCONFIDENT

    a3 = mdci_engine.compute_consensus((unjudgeable_view,), timestamp="t")
    assert a3.confidence_calibration == mdci_taxonomy.CALIBRATION_UNKNOWN


# ---------------------------------------------------------------------------
# Serialization round-trip.
# ---------------------------------------------------------------------------
def test_serialization_round_trip():
    views = _mk_views()
    assessment = mdci_engine.compute_consensus_with_explanation(views, None, timestamp="2026-07-25T09:00:00")
    as_json = mdci_serialization.assessment_to_json(assessment)
    recovered = mdci_serialization.assessment_from_json(as_json)
    assert recovered == assessment


def test_journal_and_query(tmp_path):
    views = _mk_views()
    assessment = mdci_engine.compute_consensus_with_explanation(views, None, timestamp="2026-07-25T09:00:00")
    j = mdci_journal.ConsensusJournal(tmp_path / "consensus.jsonl")
    j.record_assessment(assessment)
    recovered = j.read_assessments()
    assert recovered == [assessment]

    assert mdci_query.assessment_by_id((assessment,), assessment.assessment_id) == assessment
    assert assessment in mdci_query.assessments_by_consensus_level((assessment,), assessment.consensus_level)
    assert assessment in mdci_query.assessments_in_time_range((assessment,), "2026-07-25T08:00:00", "2026-07-25T10:00:00")
    assert assessment in mdci_query.assessments_for_domain((assessment,), "PRICE_STRUCTURE")


# ---------------------------------------------------------------------------
# AST isolation.
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
)


def _mdci_source_files():
    import bujji.msi_consensus as pkg
    package_dir = os.path.dirname(pkg.__file__)
    return sorted(glob.glob(os.path.join(package_dir, "*.py")))


def test_ast_isolation_no_forbidden_imports():
    for path in _mdci_source_files():
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


def test_ast_isolation_no_uuid4_no_unseeded_randomness_no_strategy_terms():
    for path in _mdci_source_files():
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


# ---------------------------------------------------------------------------
# Deliverable 9 — the big integration demonstration.
#
# Series 80 ("Volatility Structure") does NOT exist (see module
# docstring / Step 0 finding, also documented in docs/MSI_CONSENSUS.md).
# MOCK_VOLATILITY_DOMAIN_VIEW below is an explicit, clearly-named
# stand-in for a future real brain -- never mistaken for one.
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

# --- Translation layers (test-only, per Series 79's disclosed precedent) ---
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


def _run_full_pipeline(prices):
    snapshots, events, timestamps = _build_price_walk(prices)

    psi_assessments = psi_runner.assess_price_structure_for_episodes(tuple(snapshots), events, timestamps=timestamps)
    mssi_assessments = mssi_runner.assess_market_structure_for_episodes(tuple(snapshots), events, timestamps=timestamps)

    psi_final = psi_assessments[-1]
    mssi_final = mssi_assessments[-1]

    # --- MOCK stand-in for a not-yet-built Series 80 (Volatility
    # Structure) brain. Series 80 genuinely does not exist on disk
    # (confirmed: `ls bujji/ | grep -i volatility` finds only the
    # unrelated legacy bujji/intelligence/volatility_brain.py). This
    # is a minimal synthetic object shaped like what a real Volatility
    # Structure DomainSignal/DomainAssessmentView would look like --
    # NOT imported from any real brain, clearly named/commented so it
    # can never be mistaken for one.
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

    # --- MDCI consensus computation (real, this sprint's engine) ---
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

    # --- Series 77 synthesize(), run UNMODIFIED, SEPARATELY, from the
    # same underlying real assessments (+ the same mock), adapted into
    # DomainSignals per Series 79's established translation pattern.
    # synthesize()'s signature has no slot for a distinct "consensus"
    # concept (confirmed by reading engine.py directly) -- consensus is
    # therefore computed alongside, not threaded through, synthesize().
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

    return psi_final, mssi_final, consensus, opportunity


def test_deliverable_9_four_way_byte_identical_integration_demonstration():
    prices = _BREAKOUT_THEN_FAILED_RETEST_PRICES

    psi1, mssi1, consensus1, opp1 = _run_full_pipeline(prices)
    psi2, mssi2, consensus2, opp2 = _run_full_pipeline(prices)

    # All four outputs byte-identical across the two independent runs.
    assert psi1.assessment_id == psi2.assessment_id
    assert psi_serialization.assessment_to_dict(psi1) == psi_serialization.assessment_to_dict(psi2)

    assert mssi1.assessment_id == mssi2.assessment_id
    assert mssi_serialization.assessment_to_dict(mssi1) == mssi_serialization.assessment_to_dict(mssi2)

    assert consensus1.assessment_id == consensus2.assessment_id
    assert consensus1 == consensus2

    assert opp1.assessment_id == opp2.assessment_id
    assert opp1 == opp2

    # Sanity: consensus and opportunity are genuinely populated, not
    # placeholder/empty.
    assert consensus1.consensus_level in mdci_taxonomy.ALL_CONSENSUS_LEVELS
    assert consensus1.evidence_sufficiency in mdci_taxonomy.ALL_EVIDENCE_SUFFICIENCY_LEVELS
    assert dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE in consensus1.participating_domains
    assert opp1.opportunity_state in dse_taxonomy.ALL_OPPORTUNITY_STATES
    assert dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE in (opp1.supporting_domains + opp1.conflicting_domains)

    print("\n--- Deliverable 9 real measured output ---")
    print("psi.assessment_id  ==", psi1.assessment_id)
    print("mssi.assessment_id ==", mssi1.assessment_id)
    print("consensus.assessment_id ==", consensus1.assessment_id)
    print("consensus.consensus_level ==", consensus1.consensus_level)
    print("consensus.evidence_sufficiency ==", consensus1.evidence_sufficiency)
    print("consensus.confidence_calibration ==", consensus1.confidence_calibration)
    print("consensus.agreeing_domains ==", consensus1.agreeing_domains)
    print("consensus.conflicting_domains ==", consensus1.conflicting_domains)
    print("opportunity.assessment_id ==", opp1.assessment_id)
    print("opportunity.opportunity_state ==", opp1.opportunity_state)
    print("run1 == run2 (all four) -> True")
