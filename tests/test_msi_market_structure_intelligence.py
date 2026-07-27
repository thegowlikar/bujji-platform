"""Tests for MSI Brain 2: Market Structure Intelligence (MSSI v1) —
BUJJI Engineering Series 79.
"""
from __future__ import annotations

import ast
import glob
import os

from bujji.live_market_events import engine as lme_engine
from bujji.market_episode import engine as mee_engine
from bujji.market_observation import engine as moc_engine
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.msi_decision_synthesis import engine as dse_engine
from bujji.msi_decision_synthesis import taxonomy as dse_taxonomy
from bujji.msi_decision_synthesis.models import DomainSignal

from bujji.msi_price_structure import engine as psi_engine
from bujji.msi_price_structure import runner as psi_runner
from bujji.msi_price_structure import taxonomy as psi_taxonomy
from bujji.msi_price_structure import serialization as psi_serialization

from bujji.msi_market_structure import config as mssi_config
from bujji.msi_market_structure import engine as mssi_engine
from bujji.msi_market_structure import journal as mssi_journal
from bujji.msi_market_structure import query as mssi_query
from bujji.msi_market_structure import runner as mssi_runner
from bujji.msi_market_structure import serialization as mssi_serialization
from bujji.msi_market_structure import taxonomy as mssi_taxonomy


# ---------------------------------------------------------------------------
# Helpers — identical convention to
# tests/test_msi_price_structure_intelligence.py's `_build_price_walk`,
# using the real Series 73A/75/76 constructors.
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
    """Real Observation -> MarketEvent -> Episode chain, using real
    Series 73A/75/76 constructors, for a sequence of prices at
    1-minute increments. Returns (episode_snapshots, all_events, timestamps)."""
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


# A price sequence that establishes a resistance level (@110), tests it
# (the initial local max), confirms a breakout above it (sustained 2+
# events beyond), and then FAILS the retest (crosses back through the
# broken level) — the genuine, structurally-sound contradiction case
# (BREAKOUT_CONFIRMED simultaneous with RETEST_FAILED).
_BREAKOUT_THEN_FAILED_RETEST_PRICES = [100, 105, 100, 110, 105, 115, 120, 125, 110, 105]

# A price sequence establishing a support level tested multiple times
# without ever breaking (rejection evidence) and a real, separately
# forming resistance level, giving genuine lineage/evidence to test
# against.
_RANGE_BOUND_PRICES = [100, 90, 100, 88, 96, 88, 94, 89, 95]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
def test_assessment_determinism_same_input_same_id():
    snapshots, events, _ = _build_price_walk(_BREAKOUT_THEN_FAILED_RETEST_PRICES)
    final_episodes = snapshots[-1]
    a1 = mssi_engine.assess_market_structure(final_episodes, events, timestamp="2026-07-24T09:30:00")
    a2 = mssi_engine.assess_market_structure(final_episodes, events, timestamp="2099-01-01T00:00:00")
    assert a1.assessment_id == a2.assessment_id
    assert a1.structure_location == a2.structure_location
    assert a1.support_state == a2.support_state
    assert a1.resistance_state == a2.resistance_state
    assert a1.breakout_state == a2.breakout_state
    assert a1.breakdown_state == a2.breakdown_state
    assert a1.retest_state == a2.retest_state


def test_assessment_id_changes_when_evidence_changes():
    snapshots1, events1, _ = _build_price_walk(_BREAKOUT_THEN_FAILED_RETEST_PRICES)
    snapshots2, events2, _ = _build_price_walk(_RANGE_BOUND_PRICES)
    a1 = mssi_engine.assess_market_structure(snapshots1[-1], events1, timestamp="2026-07-24T09:30:00")
    a2 = mssi_engine.assess_market_structure(snapshots2[-1], events2, timestamp="2026-07-24T09:30:00")
    assert a1.assessment_id != a2.assessment_id


# ---------------------------------------------------------------------------
# Replay / live parity
# ---------------------------------------------------------------------------
def test_replay_live_parity():
    snapshots, events, timestamps = _build_price_walk(_BREAKOUT_THEN_FAILED_RETEST_PRICES)

    batch = mssi_runner.assess_market_structure_for_episodes(tuple(snapshots), events, timestamps=timestamps, detection_context="REPLAY")

    stream = mssi_runner.MarketStructureStream()
    live_results = []
    for episodes, ts in zip(snapshots, timestamps):
        live_results.append(stream.handle_episodes(episodes, events, timestamp=ts))

    assert len(batch) == len(live_results)
    for b, l in zip(batch, live_results):
        assert b.assessment_id == l.assessment_id
        assert b.structure_location == l.structure_location
        assert b.support_state == l.support_state
        assert b.resistance_state == l.resistance_state
        assert b.breakout_state == l.breakout_state
        assert b.breakdown_state == l.breakdown_state
        assert b.retest_state == l.retest_state
        assert b.rejection_state == l.rejection_state
        assert b.structural_balance == l.structural_balance
        assert b.contradictions == l.contradictions


# ---------------------------------------------------------------------------
# Contradiction detection — real, structurally-sound scenario: a
# CONFIRMED breakout of a resistance level whose retest subsequently
# FAILED (price crossed back through the same level) is a genuine
# structural disagreement, not a forced/fake case.
# ---------------------------------------------------------------------------
def test_contradiction_breakout_confirmed_vs_retest_failed_real_scenario():
    snapshots, events, _ = _build_price_walk(_BREAKOUT_THEN_FAILED_RETEST_PRICES)
    assessment = mssi_engine.assess_market_structure(snapshots[-1], events, timestamp="2026-07-24T09:30:00")

    assert assessment.breakout_state == mssi_taxonomy.BREAKOUT_CONFIRMED
    assert assessment.retest_state == mssi_taxonomy.RETEST_FAILED
    assert len(assessment.contradictions) >= 1
    reasons = [(c.dimension_a, c.dimension_b) for c in assessment.contradictions]
    assert ("breakout_state", "retest_state") in reasons
    assert assessment.structure_location == mssi_taxonomy.LOCATION_ABOVE_RESISTANCE


def test_detect_contradictions_direct_unit():
    contradictions = mssi_engine.detect_contradictions(
        support_state=mssi_taxonomy.SUPPORT_NONE,
        resistance_state=mssi_taxonomy.RESISTANCE_NONE,
        breakout_state=mssi_taxonomy.BREAKOUT_CONFIRMED,
        breakdown_state=mssi_taxonomy.BREAKDOWN_NONE,
        retest_state=mssi_taxonomy.RETEST_FAILED,
    )
    assert len(contradictions) == 1
    assert contradictions[0].dimension_a == "breakout_state"
    assert contradictions[0].dimension_b == "retest_state"


def test_no_contradiction_when_coherent():
    contradictions = mssi_engine.detect_contradictions(
        support_state=mssi_taxonomy.SUPPORT_ESTABLISHED,
        resistance_state=mssi_taxonomy.RESISTANCE_NONE,
        breakout_state=mssi_taxonomy.BREAKOUT_NONE,
        breakdown_state=mssi_taxonomy.BREAKDOWN_NONE,
        retest_state=mssi_taxonomy.RETEST_NONE,
    )
    assert contradictions == ()


# ---------------------------------------------------------------------------
# Confidence monotonicity
# ---------------------------------------------------------------------------
def test_confidence_decreases_monotonically_with_contradictions():
    evidence_count = 3
    levels = [mssi_engine.compute_confidence(evidence_count, n) for n in range(0, 5)]
    ranks = [mssi_taxonomy.confidence_rank(level) for level in levels]
    for i in range(1, len(ranks)):
        assert ranks[i] <= ranks[i - 1]


def test_confidence_never_negative_rank_floors_at_none():
    level = mssi_engine.compute_confidence(3, 10)
    assert level == mssi_taxonomy.CONFIDENCE_NONE


# ---------------------------------------------------------------------------
# Evidence-driven, not time-driven
# ---------------------------------------------------------------------------
def test_evidence_driven_not_time_driven():
    snapshots, events, _ = _build_price_walk(_BREAKOUT_THEN_FAILED_RETEST_PRICES)
    final_episodes = snapshots[-1]
    a_early = mssi_engine.assess_market_structure(final_episodes, events, timestamp="2026-07-24T09:30:00")
    a_late = mssi_engine.assess_market_structure(final_episodes, events, timestamp="2026-08-24T09:30:00")
    assert a_early.structure_location == a_late.structure_location
    assert a_early.assessment_id == a_late.assessment_id
    assert not hasattr(mssi_runner, "advance_time")
    assert not hasattr(mssi_engine, "advance_time")


# ---------------------------------------------------------------------------
# Evidence lineage
# ---------------------------------------------------------------------------
def test_evidence_lineage_traces_to_real_observations():
    snapshots, events, _ = _build_price_walk(_RANGE_BOUND_PRICES)
    final_episodes = snapshots[-1]
    assessment = mssi_engine.assess_market_structure(final_episodes, events, timestamp="2026-07-24T09:30:00")

    assert len(assessment.supporting_episode_ids) > 0
    assert len(assessment.supporting_observation_ids) > 0

    all_obs_ids_via_episodes = set()
    for ep in final_episodes:
        all_obs_ids_via_episodes.update(ep.originating_observation_ids)
    assert set(assessment.supporting_observation_ids).issubset(all_obs_ids_via_episodes)

    all_event_ids_via_episodes = set()
    for ep in final_episodes:
        all_event_ids_via_episodes.update(ep.originating_event_ids)
    assert set(assessment.supporting_event_ids).issubset(all_event_ids_via_episodes)


# ---------------------------------------------------------------------------
# Append-only journal
# ---------------------------------------------------------------------------
def test_journal_append_only_structurally(tmp_path):
    path = tmp_path / "mssi_journal.jsonl"
    journal = mssi_journal.MarketStructureJournal(path)
    snapshots, events, timestamps = _build_price_walk(_BREAKOUT_THEN_FAILED_RETEST_PRICES)
    mssi_runner.assess_market_structure_for_episodes(tuple(snapshots), events, timestamps=timestamps, journal=journal)
    with open(path, "rb") as fh:
        before_bytes = fh.read()
    assert len(before_bytes) > 0

    snapshots2, events2, timestamps2 = _build_price_walk(_RANGE_BOUND_PRICES)
    mssi_runner.assess_market_structure_for_episodes(tuple(snapshots2), events2, timestamps=timestamps2, journal=journal)
    with open(path, "rb") as fh:
        after_bytes = fh.read()
    assert after_bytes[: len(before_bytes)] == before_bytes
    assert len(after_bytes) > len(before_bytes)

    recovered = journal.read_assessments()
    assert len(recovered) >= 1


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------
def test_serialization_round_trip():
    snapshots, events, _ = _build_price_walk(_BREAKOUT_THEN_FAILED_RETEST_PRICES)
    assessment = mssi_engine.assess_market_structure(snapshots[-1], events, timestamp="2026-07-24T09:30:00")
    as_json = mssi_serialization.assessment_to_json(assessment)
    recovered = mssi_serialization.assessment_from_json(as_json)
    assert recovered == assessment


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------
def test_query_helpers():
    snapshots, events, timestamps = _build_price_walk(_BREAKOUT_THEN_FAILED_RETEST_PRICES)
    assessments = mssi_runner.assess_market_structure_for_episodes(tuple(snapshots), events, timestamps=timestamps)
    last = assessments[-1]
    assert mssi_query.assessment_by_id(assessments, last.assessment_id) == last
    by_loc = mssi_query.assessments_by_structure_location(assessments, last.structure_location)
    assert last in by_loc
    in_range = mssi_query.assessments_in_time_range(assessments, timestamps[0], timestamps[-1])
    assert last in in_range
    ep_id = last.supporting_episode_ids[0]
    assert last in mssi_query.assessments_for_episode(assessments, ep_id)


# ---------------------------------------------------------------------------
# Deliverable 10 — demonstration: real chain producing BOTH a
# meaningful PriceStructureAssessment (Series 78) AND a meaningful
# MarketStructureAssessment (Series 79, this sprint) from the SAME
# underlying episode/event/observation chain, both adapted into
# DomainSignals and fed TOGETHER into Series 77's real, unmodified
# synthesize(). Run twice, byte-identical for ALL assessments.
# ---------------------------------------------------------------------------
_PSI_TO_DSE_STATE = {
    psi_taxonomy.STRUCTURE_TRENDING: "Trending",
    psi_taxonomy.STRUCTURE_BALANCE: "Balanced",
    psi_taxonomy.STRUCTURE_CORRECTING: "Range",
    psi_taxonomy.STRUCTURE_TRANSITIONING: "Neutral",
    psi_taxonomy.STRUCTURE_UNKNOWN: "Neutral",
}
_CONF_TO_FLOAT = {"NONE": 0.0, "LOW": 0.3, "MODERATE": 0.6, "HIGH": 0.9}


def _mssi_to_dse_state(structure_location: str) -> str:
    """Thin translation layer (per Series 78's disclosed precedent),
    living ONLY in this demonstration/test, never inside
    bujji.msi_market_structure itself."""
    return {
        mssi_taxonomy.LOCATION_ABOVE_RESISTANCE: "Breakout",
        mssi_taxonomy.LOCATION_BELOW_SUPPORT: "Breakdown",
        mssi_taxonomy.LOCATION_INSIDE_RANGE: "Balanced",
        mssi_taxonomy.LOCATION_NEAR_SUPPORT: "Range",
        mssi_taxonomy.LOCATION_NEAR_RESISTANCE: "Range",
        mssi_taxonomy.LOCATION_AT_RETEST: "Neutral",
        mssi_taxonomy.LOCATION_UNKNOWN: "Neutral",
    }[structure_location]


def _run_three_brain_pipeline(prices):
    snapshots, events, timestamps = _build_price_walk(prices)

    psi_assessments = psi_runner.assess_price_structure_for_episodes(tuple(snapshots), events, timestamps=timestamps)
    mssi_assessments = mssi_runner.assess_market_structure_for_episodes(tuple(snapshots), events, timestamps=timestamps)

    psi_final = psi_assessments[-1]
    mssi_final = mssi_assessments[-1]

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
    mock_liquidity_signal = DomainSignal(
        domain_name=dse_taxonomy.DOMAIN_LIQUIDITY, state="ADEQUATE", confidence=0.8, evidence_ids=("MOCK-LIQ-1",),
    )

    opportunity = dse_engine.synthesize(
        (price_structure_signal, support_resistance_signal, mock_liquidity_signal),
        previous_assessment=None,
        episode_ids=tuple(sorted(set(psi_final.supporting_episode_ids) | set(mssi_final.supporting_episode_ids))),
        timestamp="2026-07-24T09:30:00",
    )
    return psi_assessments, mssi_assessments, opportunity


def test_deliverable_10_three_brain_demonstration_deterministic_and_synthesizes():
    prices = _BREAKOUT_THEN_FAILED_RETEST_PRICES

    psi1, mssi1, opp1 = _run_three_brain_pipeline(prices)
    psi2, mssi2, opp2 = _run_three_brain_pipeline(prices)

    assert len(psi1) == len(psi2)
    assert len(mssi1) == len(mssi2)
    for a1, a2 in zip(psi1, psi2):
        assert psi_serialization.assessment_to_dict(a1) == psi_serialization.assessment_to_dict(a2)
    for a1, a2 in zip(mssi1, mssi2):
        assert mssi_serialization.assessment_to_dict(a1) == mssi_serialization.assessment_to_dict(a2)

    # Both brains' final assessment ids must be byte-identical run-to-run.
    assert psi1[-1].assessment_id == psi2[-1].assessment_id
    assert mssi1[-1].assessment_id == mssi2[-1].assessment_id

    # The resulting MarketOpportunityAssessment must ALSO be
    # byte-identical run-to-run — the stronger composition proof this
    # sprint requires (78+79+77 together, not just 78 alone with 77).
    assert opp1.assessment_id == opp2.assessment_id
    assert opp1 == opp2

    assert opp1.opportunity_state in dse_taxonomy.ALL_OPPORTUNITY_STATES
    assert (
        dse_taxonomy.DOMAIN_PRICE_STRUCTURE in opp1.supporting_domains
        or dse_taxonomy.DOMAIN_PRICE_STRUCTURE in opp1.conflicting_domains
    )
    assert (
        dse_taxonomy.DOMAIN_SUPPORT_RESISTANCE in opp1.supporting_domains
        or dse_taxonomy.DOMAIN_SUPPORT_RESISTANCE in opp1.conflicting_domains
    )


# ---------------------------------------------------------------------------
# AST isolation — same forbidden-import/no-uuid4/no-randomness/no-
# trading-terms checks as Series 78, adapted to this package's path.
# ---------------------------------------------------------------------------
_FORBIDDEN_MODULE_PREFIXES = (
    "mic_v2",
    "bujji.mic_replay",
    "bujji.production_runtime",
    "bujji.trading_brain",
    "bujji.strategy_selector",
    "fyers_apiv3",
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


def _mssi_source_files():
    import bujji.msi_market_structure as pkg
    package_dir = os.path.dirname(pkg.__file__)
    return sorted(glob.glob(os.path.join(package_dir, "*.py")))


def test_ast_isolation_no_forbidden_imports():
    for path in _mssi_source_files():
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


def test_ast_isolation_no_uuid4_no_unseeded_randomness():
    for path in _mssi_source_files():
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


def test_ast_isolation_no_forbidden_trading_terms():
    for path in _mssi_source_files():
        with open(path, "r") as fh:
            source = fh.read().lower()
        for term in _FORBIDDEN_TRADING_TERMS:
            assert term not in source, f"{path} contains forbidden trading term {term!r}"
