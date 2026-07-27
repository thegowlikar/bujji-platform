"""Tests for MSI Brain 1: Price Structure Intelligence (PSI v1) —
BUJJI Engineering Series 78.
"""
from __future__ import annotations

import ast
import glob
import hashlib
import json
import os

from bujji.live_market_events import engine as lme_engine
from bujji.live_market_events.models import MarketEvent, MarketEventProvenance
from bujji.market_episode import engine as mee_engine
from bujji.market_episode import taxonomy as mee_taxonomy
from bujji.market_observation import engine as moc_engine
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.msi_decision_synthesis import engine as dse_engine
from bujji.msi_decision_synthesis import taxonomy as dse_taxonomy
from bujji.msi_decision_synthesis.models import DomainSignal

from bujji.msi_price_structure import config as psi_config
from bujji.msi_price_structure import engine as psi_engine
from bujji.msi_price_structure import journal as psi_journal
from bujji.msi_price_structure import query as psi_query
from bujji.msi_price_structure import runner as psi_runner
from bujji.msi_price_structure import serialization as psi_serialization
from bujji.msi_price_structure import taxonomy as psi_taxonomy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mk_event(event_type, timestamp, obs_ids=("OBS-a",), detail=None, event_id_suffix=""):
    detail = detail or {"old_value": 100.0, "new_value": 101.0, "delta": 1.0}
    provenance = MarketEventProvenance(originating_source="test", detection_context="LIVE", schema_version="1.0.0")
    canonical_detail = json.dumps(detail, sort_keys=True, default=repr)
    seed = "|".join([event_type, *obs_ids, canonical_detail, timestamp, event_id_suffix])
    event_id = "MEVT-" + hashlib.md5(seed.encode()).hexdigest()[:24]
    return MarketEvent(
        event_id=event_id, event_type=event_type, timestamp=timestamp,
        originating_observation_ids=tuple(obs_ids), detail=dict(detail),
        provenance=provenance, schema_version="1.0.0",
    )


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
    1-minute increments. Returns (episodes_over_time, all_events)."""
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


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
def test_assessment_determinism_same_input_same_id():
    snapshots, events, _ = _build_price_walk([100, 101, 102, 103, 104, 105])
    final_episodes = snapshots[-1]
    a1 = psi_engine.assess_price_structure(final_episodes, events, timestamp="2026-07-24T09:30:00")
    a2 = psi_engine.assess_price_structure(final_episodes, events, timestamp="2099-01-01T00:00:00")
    assert a1.assessment_id == a2.assessment_id
    assert a1.structure_state == a2.structure_state
    assert a1.trend_state == a2.trend_state
    assert a1.swing_state == a2.swing_state
    assert a1.balance_state == a2.balance_state


def test_assessment_id_changes_when_evidence_changes():
    snapshots, events, _ = _build_price_walk([100, 101, 102, 103, 104, 105])
    snapshots2, events2, _ = _build_price_walk([100, 99, 98, 97, 96, 95])
    a1 = psi_engine.assess_price_structure(snapshots[-1], events, timestamp="2026-07-24T09:30:00")
    a2 = psi_engine.assess_price_structure(snapshots2[-1], events2, timestamp="2026-07-24T09:30:00")
    assert a1.assessment_id != a2.assessment_id


# ---------------------------------------------------------------------------
# Replay / live parity
# ---------------------------------------------------------------------------
def test_replay_live_parity():
    snapshots, events, timestamps = _build_price_walk([100, 101, 102, 103, 99, 98])

    batch = psi_runner.assess_price_structure_for_episodes(tuple(snapshots), events, timestamps=timestamps, detection_context="REPLAY")

    stream = psi_runner.PriceStructureStream()
    live_results = []
    for episodes, ts in zip(snapshots, timestamps):
        live_results.append(stream.handle_episodes(episodes, events, timestamp=ts))

    assert len(batch) == len(live_results)
    for b, l in zip(batch, live_results):
        assert b.assessment_id == l.assessment_id
        assert b.structure_state == l.structure_state
        assert b.trend_state == l.trend_state
        assert b.swing_state == l.swing_state
        assert b.compression_state == l.compression_state
        assert b.expansion_state == l.expansion_state
        assert b.balance_state == l.balance_state
        assert b.contradictions == l.contradictions


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------
def test_contradiction_detected_trend_vs_balance():
    contradictions = psi_engine.detect_contradictions(
        trend_state=psi_taxonomy.TREND_ESTABLISHED,
        swing_state=psi_taxonomy.SWING_CONFIRMED,
        balance_state=psi_taxonomy.BALANCE_IN_BALANCE,
        compression_state=psi_taxonomy.COMPRESSION_NOT_DETECTED,
        expansion_state=psi_taxonomy.EXPANSION_NOT_DETECTED,
    )
    assert len(contradictions) == 1
    assert contradictions[0].dimension_a == "trend_state"
    assert contradictions[0].dimension_b == "balance_state"


def test_no_contradiction_when_coherent():
    contradictions = psi_engine.detect_contradictions(
        trend_state=psi_taxonomy.TREND_ESTABLISHED,
        swing_state=psi_taxonomy.SWING_CONFIRMED,
        balance_state=psi_taxonomy.BALANCE_IMBALANCED,
        compression_state=psi_taxonomy.COMPRESSION_NOT_DETECTED,
        expansion_state=psi_taxonomy.EXPANSION_EARLY,
    )
    assert contradictions == ()
    assert psi_engine.derive_structure_integrity(contradictions) == psi_taxonomy.INTEGRITY_COHERENT


# ---------------------------------------------------------------------------
# Confidence monotonicity (mirrors Series 77's precedent)
# ---------------------------------------------------------------------------
def test_confidence_decreases_monotonically_with_contradictions():
    evidence_count = 6
    levels = [psi_engine.compute_confidence(evidence_count, n) for n in range(0, 5)]
    ranks = [psi_taxonomy.confidence_rank(level) for level in levels]
    for i in range(1, len(ranks)):
        assert ranks[i] <= ranks[i - 1]


def test_confidence_never_negative_rank_floors_at_none():
    level = psi_engine.compute_confidence(6, 10)
    assert level == psi_taxonomy.CONFIDENCE_NONE


# ---------------------------------------------------------------------------
# Evidence-driven, not time-driven
# ---------------------------------------------------------------------------
def test_evidence_driven_not_time_driven():
    """Advancing time alone, with no new episode/event, never changes
    structure_state -- there is no time-advance entrypoint in this
    package at all, so re-invoking assess_price_structure with the
    SAME episodes/events (only a different `timestamp` argument, which
    is metadata-only and excluded from assessment_id) must produce an
    identical structure_state and assessment_id."""
    snapshots, events, _ = _build_price_walk([100, 101, 102, 103, 104, 105])
    final_episodes = snapshots[-1]
    a_early = psi_engine.assess_price_structure(final_episodes, events, timestamp="2026-07-24T09:30:00")
    a_late = psi_engine.assess_price_structure(final_episodes, events, timestamp="2026-08-24T09:30:00")
    assert a_early.structure_state == a_late.structure_state
    assert a_early.assessment_id == a_late.assessment_id
    assert not hasattr(psi_runner, "advance_time")
    assert not hasattr(psi_engine, "advance_time")


# ---------------------------------------------------------------------------
# Evidence lineage (genuine cross-series integration)
# ---------------------------------------------------------------------------
def test_evidence_lineage_traces_to_real_observations():
    snapshots, events, _ = _build_price_walk([100, 101, 102, 101, 100])
    final_episodes = snapshots[-1]
    assessment = psi_engine.assess_price_structure(final_episodes, events, timestamp="2026-07-24T09:30:00")

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
    path = tmp_path / "psi_journal.jsonl"
    journal = psi_journal.PriceStructureJournal(path)
    snapshots, events, timestamps = _build_price_walk([100, 101, 102, 103])
    psi_runner.assess_price_structure_for_episodes(tuple(snapshots), events, timestamps=timestamps, journal=journal)
    with open(path, "rb") as fh:
        before_bytes = fh.read()
    assert len(before_bytes) > 0

    snapshots2, events2, timestamps2 = _build_price_walk([200, 201, 202])
    psi_runner.assess_price_structure_for_episodes(tuple(snapshots2), events2, timestamps=timestamps2, journal=journal)
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
    snapshots, events, _ = _build_price_walk([100, 101, 102, 103, 104])
    assessment = psi_engine.assess_price_structure(snapshots[-1], events, timestamp="2026-07-24T09:30:00")
    as_json = psi_serialization.assessment_to_json(assessment)
    recovered = psi_serialization.assessment_from_json(as_json)
    assert recovered == assessment


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------
def test_query_helpers():
    snapshots, events, timestamps = _build_price_walk([100, 101, 102, 103, 104, 105])
    assessments = psi_runner.assess_price_structure_for_episodes(tuple(snapshots), events, timestamps=timestamps)
    last = assessments[-1]
    assert psi_query.assessment_by_id(assessments, last.assessment_id) == last
    by_state = psi_query.assessments_by_structure_state(assessments, last.structure_state)
    assert last in by_state
    in_range = psi_query.assessments_in_time_range(assessments, timestamps[0], timestamps[-1])
    assert last in in_range
    ep_id = last.supporting_episode_ids[0]
    assert last in psi_query.assessments_for_episode(assessments, ep_id)


# ---------------------------------------------------------------------------
# Deliverable 10 — demonstration: real chain, run twice byte-identical,
# and genuine integration with Series 77's real synthesize().
# ---------------------------------------------------------------------------
def test_deliverable_10_demonstration_deterministic_and_synthesizes():
    prices = [100, 101, 102, 103, 104, 105, 104, 103]
    snapshots1, events1, timestamps1 = _build_price_walk(prices)
    snapshots2, events2, timestamps2 = _build_price_walk(prices)

    assessments1 = psi_runner.assess_price_structure_for_episodes(tuple(snapshots1), events1, timestamps=timestamps1)
    assessments2 = psi_runner.assess_price_structure_for_episodes(tuple(snapshots2), events2, timestamps=timestamps2)

    assert len(assessments1) == len(assessments2)
    for a1, a2 in zip(assessments1, assessments2):
        assert psi_serialization.assessment_to_dict(a1) == psi_serialization.assessment_to_dict(a2)

    final = assessments1[-1]

    # Manual adaptation (per this sprint's explicit Step 3 instruction):
    # PSI's own structure_state vocabulary (UNKNOWN/BALANCE/TRENDING/
    # CORRECTING/TRANSITIONING) is NOT the same closed vocabulary DSE's
    # `config.STATE_LEAN_MAP` recognizes (Title-case: "Trending",
    # "Balanced", "Range", "Neutral", ...) -- this is exactly the kind
    # of thin, external translation layer Series 77's own models.py
    # docstring anticipates ("each will need to ... be adapted to it by
    # a thin translation layer outside this package"). This mapping
    # lives ONLY in this test/demonstration, never inside
    # bujji.msi_price_structure itself.
    _PSI_TO_DSE_STATE = {
        psi_taxonomy.STRUCTURE_TRENDING: "Trending",
        psi_taxonomy.STRUCTURE_BALANCE: "Balanced",
        psi_taxonomy.STRUCTURE_CORRECTING: "Range",
        psi_taxonomy.STRUCTURE_TRANSITIONING: "Neutral",
        psi_taxonomy.STRUCTURE_UNKNOWN: "Neutral",
    }
    price_structure_signal = DomainSignal(
        domain_name=dse_taxonomy.DOMAIN_PRICE_STRUCTURE,
        state=_PSI_TO_DSE_STATE[final.structure_state],
        confidence={"NONE": 0.0, "LOW": 0.3, "MODERATE": 0.6, "HIGH": 0.9}[final.confidence],
        evidence_ids=final.supporting_observation_ids,
    )
    mock_signals = (
        price_structure_signal,
        DomainSignal(domain_name=dse_taxonomy.DOMAIN_VOLATILITY_STRUCTURE, state="CONTRACTING", confidence=0.5, evidence_ids=("MOCK-VOL-1",)),
        DomainSignal(domain_name=dse_taxonomy.DOMAIN_LIQUIDITY, state="ADEQUATE", confidence=0.8, evidence_ids=("MOCK-LIQ-1",)),
    )

    opportunity_assessment = dse_engine.synthesize(
        mock_signals, previous_assessment=None, episode_ids=final.supporting_episode_ids,
        timestamp="2026-07-24T09:30:00",
    )
    assert opportunity_assessment.opportunity_state in dse_taxonomy.ALL_OPPORTUNITY_STATES
    assert dse_taxonomy.DOMAIN_PRICE_STRUCTURE in opportunity_assessment.supporting_domains or \
        dse_taxonomy.DOMAIN_PRICE_STRUCTURE in opportunity_assessment.conflicting_domains
    assert opportunity_assessment.assessment_id


# ---------------------------------------------------------------------------
# AST isolation
# ---------------------------------------------------------------------------
_FORBIDDEN_MODULE_PREFIXES = (
    "mic_v2",
    "bujji.mic_replay",
    "bujji.production_runtime",
    "bujji.trading_brain",
    "bujji.strategy_selector",
    "fyers_apiv3",
)


# Note: this list deliberately does NOT include the bare word "strategy" --
# every source file's isolation docstring legitimately mentions
# `bujji.strategy_selector` as a FORBIDDEN IMPORT (see
# `_FORBIDDEN_MODULE_PREFIXES` above and `test_ast_isolation_no_forbidden_imports`),
# which would false-positive a bare substring scan. The terms below are
# specific enough (strike price, PoP, direction prediction, optimization)
# that they cannot appear in a package that is genuinely descriptive-only.
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


def _psi_source_files():
    import bujji.msi_price_structure as pkg
    package_dir = os.path.dirname(pkg.__file__)
    return sorted(glob.glob(os.path.join(package_dir, "*.py")))


def test_ast_isolation_no_forbidden_imports():
    for path in _psi_source_files():
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
    for path in _psi_source_files():
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
                assert node.module not in ("random", "uuid"), f"{path} imports from {node.module}"


def test_ast_isolation_no_forbidden_trading_terms():
    for path in _psi_source_files():
        with open(path, "r") as fh:
            source = fh.read().lower()
        for term in _FORBIDDEN_TRADING_TERMS:
            assert term not in source, f"{path} contains forbidden trading-decision term {term!r}"
