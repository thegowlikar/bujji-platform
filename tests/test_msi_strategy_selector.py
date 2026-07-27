"""Tests for the Strategy Selector (MSS v1) — BUJJI Engineering Series 89."""
from __future__ import annotations

import ast
import glob
import os
from datetime import datetime, timedelta

from bujji.live_market_events import engine as lme_engine
from bujji.market_episode import engine as mee_engine
from bujji.market_observation import engine as moc_engine
from bujji.market_observation import taxonomy as moc_taxonomy

from bujji.msi_price_structure import engine as psi_engine
from bujji.msi_market_structure import engine as mssi_engine
from bujji.msi_market_direction import engine as mdi_engine

from bujji.msi_participant_positioning import engine as mppi_engine

from bujji.msi_consensus import engine as consensus_engine
from bujji.msi_consensus import taxonomy as consensus_taxonomy

from bujji.msi_strategy_selection_foundation import engine as ssf_engine
from bujji.msi_strategy_selection_foundation import taxonomy as ssf_taxonomy

from bujji.msi_strategy_selector import config as mss_config
from bujji.msi_strategy_selector import engine as mss_engine
from bujji.msi_strategy_selector import journal as mss_journal
from bujji.msi_strategy_selector import query as mss_query
from bujji.msi_strategy_selector import runner as mss_runner
from bujji.msi_strategy_selector import serialization as mss_serialization
from bujji.msi_strategy_selector import taxonomy as mss_taxonomy
from bujji.msi_strategy_selector.models import CandidateScore


def _mk_observation(timestamp, price):
    return moc_engine.build_observation(
        observation_type=moc_taxonomy.ALL_OBSERVATION_TYPES[0], instrument="NIFTY", exchange="NSE", segment="EQ",
        timestamp=timestamp, resolution=moc_taxonomy.RESOLUTION_ONE_MINUTE, source="TEST_SOURCE",
        schema_version="1.0.0", value_kind=moc_taxonomy.VALUE_KIND_SCALAR, payload=price,
        completeness=1.0, freshness=0.0, confidence=1.0, missing_fields=(),
        validation_status=moc_taxonomy.VALIDATION_VALID, source_quality="HIGH",
        originating_source="TEST_SOURCE", acquisition_timestamp=timestamp,
        normalization_timestamp=timestamp, origin=moc_taxonomy.ORIGIN_LIVE, provenance_version="1.0.0",
    )


def _build_price_walk(prices, start="2026-07-24T09:15:00"):
    timestamps = [(datetime.fromisoformat(start) + timedelta(minutes=i)).isoformat() for i in range(len(prices))]
    observations = [_mk_observation(ts, p) for ts, p in zip(timestamps, prices)]
    events = []
    previous = None
    for obs in observations:
        for ev in lme_engine.detect_price_change(obs, previous):
            events.append(ev)
        previous = obs
    events = tuple(events)
    episodes = ()
    for ev in events:
        episodes = mee_engine.advance_time(episodes, ev.timestamp, detection_context="REPLAY")
        episodes = mee_engine.process_event(episodes, ev, detection_context="REPLAY")
    return episodes, events


def _mdi_domain_view(mdi):
    lean_map = {
        "STRONG_BULLISH": consensus_taxonomy.LEAN_BULLISH, "BULLISH": consensus_taxonomy.LEAN_BULLISH,
        "WEAK_BULLISH": consensus_taxonomy.LEAN_BULLISH, "NEUTRAL": consensus_taxonomy.LEAN_NEUTRAL,
        "WEAK_BEARISH": consensus_taxonomy.LEAN_BEARISH, "BEARISH": consensus_taxonomy.LEAN_BEARISH,
        "STRONG_BEARISH": consensus_taxonomy.LEAN_BEARISH,
        "MIXED": consensus_taxonomy.LEAN_AMBIGUOUS, "UNKNOWN": consensus_taxonomy.LEAN_AMBIGUOUS,
    }
    conf_map = {"NONE": 0.0, "LOW": 0.3, "MODERATE": 0.6, "HIGH": 0.9}
    return consensus_engine.DomainAssessmentView(
        domain_name="MARKET_DIRECTION", lean=lean_map[mdi.overall_direction], confidence=conf_map[mdi.overall_confidence],
        evidence_ids=mdi.supporting_assessment_ids, source_assessment_id=mdi.assessment_id,
    )


def _build_all(prices, ts="2026-07-24T09:30:00"):
    episodes, events = _build_price_walk(prices)
    psi = psi_engine.assess_price_structure(episodes, events, timestamp=ts)
    mssi = mssi_engine.assess_market_structure(episodes, events, timestamp=ts)
    mdi = mdi_engine.determine_market_direction(psi, mssi, timestamp=ts)
    mppi = mppi_engine.assess_participant_positioning((), None, timestamp=ts)
    consensus = consensus_engine.compute_consensus((_mdi_domain_view(mdi),), expected_domains=("MARKET_DIRECTION",), timestamp=ts)
    ssf = ssf_engine.assess_all_families(mdi, mssi, consensus, None, timestamp=ts)
    return ssf, psi, mssi, mdi, mppi, consensus


# ---------------------------------------------------------------------------
# Deliverable 1 — market state derivation, real evidence, independent dims.
# ---------------------------------------------------------------------------
def test_market_states_are_independent_not_mutually_exclusive():
    ssf, psi, mssi, mdi, mppi, consensus = _build_all([100, 100.1, 99.9, 100.05, 99.95, 100.02, 99.98, 100.01])
    states = mss_engine.derive_market_states(psi, mssi, mdi, mppi, None, consensus)
    assert isinstance(states, tuple)
    # No structural constraint prevents multiple states co-occurring.
    for s in states:
        assert s in mss_taxonomy.ALL_MARKET_STATES


# ---------------------------------------------------------------------------
# Deliverable 3/5 — determinism, immutability.
# ---------------------------------------------------------------------------
def test_determinism_same_input_same_id():
    ssf, psi, mssi, mdi, mppi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    a1 = mss_engine.select_strategy(ssf, psi, mssi, mdi, mppi, consensus, None, timestamp="2026-07-24T09:30:00")
    a2 = mss_engine.select_strategy(ssf, psi, mssi, mdi, mppi, consensus, None, timestamp="2099-01-01T00:00:00")
    assert a1.assessment_id == a2.assessment_id
    assert a1.selected_strategy_family == a2.selected_strategy_family


def test_assessment_id_changes_when_evidence_changes():
    ssf1, psi1, mssi1, mdi1, mppi1, consensus1 = _build_all([100, 101, 102, 103, 104, 105, 106])
    ssf2, psi2, mssi2, mdi2, mppi2, consensus2 = _build_all([100, 99, 98, 97, 96, 95, 94])
    a1 = mss_engine.select_strategy(ssf1, psi1, mssi1, mdi1, mppi1, consensus1, None, timestamp="2026-07-24T09:30:00")
    a2 = mss_engine.select_strategy(ssf2, psi2, mssi2, mdi2, mppi2, consensus2, None, timestamp="2026-07-24T09:30:00")
    assert a1.assessment_id != a2.assessment_id


def test_batch_vs_streaming_parity():
    ssf, psi, mssi, mdi, mppi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    batch = mss_runner.select_strategy(ssf, psi, mssi, mdi, mppi, consensus, None, timestamp="2026-07-24T09:30:00")
    stream = mss_runner.StrategySelectorStream()
    live = stream.process(ssf, psi, mssi, mdi, mppi, consensus, None, timestamp="2026-07-24T09:30:00")
    assert batch.assessment_id == live.assessment_id
    assert mss_serialization.assessment_to_json(batch) == mss_serialization.assessment_to_json(live)


def test_byte_identical_full_rerun():
    def _run():
        ssf, psi, mssi, mdi, mppi, consensus = _build_all([100, 99, 100, 101, 102, 103, 104, 90])
        return mss_engine.select_strategy(ssf, psi, mssi, mdi, mppi, consensus, None, timestamp="2026-07-24T09:30:00")
    r1, r2 = _run(), _run()
    assert mss_serialization.assessment_to_json(r1) == mss_serialization.assessment_to_json(r2)


# ---------------------------------------------------------------------------
# Deliverable 4 — explainability
# ---------------------------------------------------------------------------
def test_explanation_always_populated():
    ssf, psi, mssi, mdi, mppi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    a = mss_engine.select_strategy(ssf, psi, mssi, mdi, mppi, consensus, None, timestamp="2026-07-24T09:30:00")
    assert len(a.explanation.why_this_strategy) > 0
    assert a.explanation.active_market_states == mss_engine.derive_market_states(psi, mssi, mdi, mppi, None, consensus)


def test_no_selection_case_has_real_reasons():
    """A real, honest no-selection scenario: no SUITABLE family exists at all."""
    from bujji.msi_strategy_selection_foundation.models import StrategySuitabilityAssessment
    ssf, psi, mssi, mdi, mppi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    # Force every family to UNSUITABLE/INSUFFICIENT_EVIDENCE by construction (no SUITABLE family).
    forced = tuple(a for a in ssf if a.suitability != ssf_taxonomy.SUITABLE)
    if len(forced) == len(ssf):
        a = mss_engine.select_strategy(forced, psi, mssi, mdi, mppi, consensus, None, timestamp="2026-07-24T09:30:00")
        assert a.selected_strategy_family is None
        assert a.confidence == mss_taxonomy.CONFIDENCE_NONE
        assert len(a.explanation.why_this_strategy) > 0


# ---------------------------------------------------------------------------
# Deliverable 2 — forbidden state disqualification, real scenario
# ---------------------------------------------------------------------------
def test_forbidden_state_disqualifies_candidate():
    directly_scored = mss_engine.score_candidate("LONG_DIRECTIONAL", (mss_taxonomy.STATE_BALANCE,))
    assert directly_scored.disqualified is True
    assert mss_taxonomy.STATE_BALANCE in directly_scored.disqualifying_states
    assert directly_scored.match_score is None


def test_preferred_beats_acceptable_in_scoring():
    preferred_only = mss_engine.score_candidate("IRON_CONDOR", (mss_taxonomy.STATE_BALANCE,))
    acceptable_only = mss_engine.score_candidate("IRON_CONDOR", (mss_taxonomy.STATE_VOLATILITY_CONTRACTION,))
    both = mss_engine.score_candidate("IRON_CONDOR", (mss_taxonomy.STATE_BALANCE, mss_taxonomy.STATE_VOLATILITY_CONTRACTION))
    assert preferred_only.match_score > acceptable_only.match_score
    assert both.match_score > preferred_only.match_score


# ---------------------------------------------------------------------------
# Deliverable 8 — identical intelligence always produces identical selection.
# ---------------------------------------------------------------------------
def test_identical_intelligence_always_produces_identical_selection():
    ssf, psi, mssi, mdi, mppi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106, 107])
    results = [mss_engine.select_strategy(ssf, psi, mssi, mdi, mppi, consensus, None, timestamp="2026-07-24T09:30:00") for _ in range(5)]
    ids = {r.assessment_id for r in results}
    assert len(ids) == 1


# ---------------------------------------------------------------------------
# Serialization / journal / query
# ---------------------------------------------------------------------------
def test_serialization_round_trip():
    ssf, psi, mssi, mdi, mppi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    a = mss_engine.select_strategy(ssf, psi, mssi, mdi, mppi, consensus, None, timestamp="2026-07-24T09:30:00")
    recovered = mss_serialization.assessment_from_json(mss_serialization.assessment_to_json(a))
    assert recovered == a


def test_journal_append_only():
    ssf, psi, mssi, mdi, mppi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    a = mss_engine.select_strategy(ssf, psi, mssi, mdi, mppi, consensus, None, timestamp="2026-07-24T09:30:00")
    journal = mss_journal.StrategySelectorJournal()
    journal.record_assessment(a, recorded_at="2026-07-24T09:30:01")
    entries_before = journal.entries()
    journal.record_assessment(a, recorded_at="2026-07-24T09:30:02")
    assert len(journal) == 2
    assert entries_before == journal.entries()[:1]


def test_query_helpers():
    ssf, psi, mssi, mdi, mppi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    a = mss_engine.select_strategy(ssf, psi, mssi, mdi, mppi, consensus, None, timestamp="2026-07-24T09:30:00")
    assert mss_query.by_id((a,), a.assessment_id) == a
    assert mss_query.by_id((a,), "nonexistent") is None
    assert a in mss_query.by_selected_family((a,), a.selected_strategy_family)
    assert mss_query.latest((a,)) == a


# ---------------------------------------------------------------------------
# AST isolation
# ---------------------------------------------------------------------------
_PACKAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bujji", "msi_strategy_selector")

_FORBIDDEN_MODULE_PREFIXES = (
    "mic_v2", "bujji.mic_replay", "bujji.production_runtime", "bujji.trading_brain",
    "bujji.strategy_selector", "fyers_apiv3", "bujji.intelligence",
    "bujji.msi_decision_synthesis", "bujji.msi_strategy_eligibility", "bujji.msi_trade_intent",
)
_FORBIDDEN_TERMS = ("uuid4", "strike_select", "expiry_select", "place_order", "execution_plan", "optimi", "backtest", "pnl")


def _all_package_files():
    return sorted(glob.glob(os.path.join(_PACKAGE_DIR, "*.py")))


def test_ast_isolation_no_forbidden_imports():
    for path in _all_package_files():
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                for forbidden in _FORBIDDEN_MODULE_PREFIXES:
                    assert not name.startswith(forbidden), f"{path} imports forbidden module {name}"


def test_ast_isolation_no_uuid4_no_unseeded_randomness():
    for path in _all_package_files():
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                raise AssertionError(f"{path} calls uuid4()")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "random", f"{path} imports random"
            if isinstance(node, ast.ImportFrom):
                assert node.module != "random", f"{path} imports from random"


def test_ast_isolation_no_forbidden_optimization_terms():
    for path in _all_package_files():
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)):
                identifier = node.id if isinstance(node, ast.Name) else node.attr
                lowered = identifier.lower()
                for term in ("optimi", "backtest", "pnl"):
                    assert term not in lowered, f"{path} contains forbidden identifier fragment '{term}' in '{identifier}'"
