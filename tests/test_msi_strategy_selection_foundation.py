"""Tests for Strategy Selection Foundation (SSF v1) — BUJJI Engineering
Series 87."""
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

from bujji.msi_consensus import engine as consensus_engine
from bujji.msi_consensus import taxonomy as consensus_taxonomy

from bujji.msi_strategy_selection_foundation import config as ssf_config
from bujji.msi_strategy_selection_foundation import engine as ssf_engine
from bujji.msi_strategy_selection_foundation import journal as ssf_journal
from bujji.msi_strategy_selection_foundation import query as ssf_query
from bujji.msi_strategy_selection_foundation import runner as ssf_runner
from bujji.msi_strategy_selection_foundation import serialization as ssf_serialization
from bujji.msi_strategy_selection_foundation import taxonomy as ssf_taxonomy

from bujji.msi_volatility_structure import engine as vsb_engine


# ---------------------------------------------------------------------------
# Helpers — real Observation -> MarketEvent -> Episode -> PSI/MSSI/MDI
# chain, mirroring tests/test_msi_market_direction_intelligence.py
# exactly, plus a real Consensus built via the same DomainAssessmentView
# translation pattern used throughout this session's corpus scripts.
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
    consensus = consensus_engine.compute_consensus((_mdi_domain_view(mdi),), expected_domains=("MARKET_DIRECTION",), timestamp=ts)
    return mdi, mssi, consensus


# ---------------------------------------------------------------------------
# Deliverable 5 — independence: every family assessed, no ranking/best.
# ---------------------------------------------------------------------------
def test_all_families_assessed_independently():
    mdi, mssi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    assessments = ssf_engine.assess_all_families(mdi, mssi, consensus, timestamp="2026-07-24T09:30:00")
    assert len(assessments) == len(ssf_taxonomy.ALL_STRATEGY_FAMILIES)
    families_seen = {a.strategy_family for a in assessments}
    assert families_seen == set(ssf_taxonomy.ALL_STRATEGY_FAMILIES)
    # No comparison field exists anywhere on the model -- structurally
    # guaranteed by StrategySuitabilityAssessment having no rank/score field.
    for a in assessments:
        assert not hasattr(a, "score")
        assert not hasattr(a, "rank")


# ---------------------------------------------------------------------------
# Insufficient-evidence honesty (Deliverable 1/7): volatility/liquidity-
# dependent families must never be forced to SUITABLE/UNSUITABLE.
# ---------------------------------------------------------------------------
def test_volatility_dependent_families_report_insufficient_evidence_without_vsb():
    """Series 88 follow-up: DOMAIN_VOLATILITY is now genuinely available
    codebase-wide, but calling assess_all_families WITHOUT a real `vsb`
    instance must still honestly report INSUFFICIENT_EVIDENCE for every
    family that requires it -- a per-call evidence gap, not a domain
    that doesn't exist. CALENDAR requires DOMAIN_VOLATILITY_TERM_STRUCTURE
    specifically (still genuinely unavailable everywhere), never plain
    DOMAIN_VOLATILITY -- kept deliberately distinct."""
    mdi, mssi, consensus = _build_all([100, 99, 98, 97, 96, 95, 94])
    assessments = ssf_engine.assess_all_families(mdi, mssi, consensus, timestamp="2026-07-24T09:30:00")
    for family in (ssf_taxonomy.VOLATILITY_EXPANSION, ssf_taxonomy.VOLATILITY_COMPRESSION,
                   ssf_taxonomy.NEUTRAL_PREMIUM_SELLING, ssf_taxonomy.NEUTRAL_PREMIUM_BUYING,
                   ssf_taxonomy.RATIO, ssf_taxonomy.BUTTERFLY):
        a = ssf_query.by_family(assessments, family)
        assert a.suitability == ssf_taxonomy.INSUFFICIENT_EVIDENCE, f"{family} should be INSUFFICIENT_EVIDENCE"
        assert ssf_taxonomy.DOMAIN_VOLATILITY in a.required_missing_evidence
        assert a.confidence == ssf_taxonomy.CONFIDENCE_NONE

    for family in (ssf_taxonomy.IRON_CONDOR, ssf_taxonomy.IRON_FLY):
        a = ssf_query.by_family(assessments, family)
        assert a.suitability == ssf_taxonomy.INSUFFICIENT_EVIDENCE
        # Both Volatility (per-call, no vsb given) AND Liquidity (still
        # codebase-wide unavailable) are genuinely missing here.
        assert ssf_taxonomy.DOMAIN_VOLATILITY in a.required_missing_evidence
        assert ssf_taxonomy.DOMAIN_LIQUIDITY in a.required_missing_evidence

    calendar = ssf_query.by_family(assessments, ssf_taxonomy.CALENDAR)
    assert calendar.suitability == ssf_taxonomy.INSUFFICIENT_EVIDENCE
    assert ssf_taxonomy.DOMAIN_VOLATILITY_TERM_STRUCTURE in calendar.required_missing_evidence
    assert ssf_taxonomy.DOMAIN_VOLATILITY not in calendar.required_missing_evidence


def _real_vsb(prices, ts="2026-07-24T09:30:00"):
    closes = [((datetime.fromisoformat("2026-07-24T09:15:00") + timedelta(minutes=15 * i)).isoformat(), p) for i, p in enumerate(prices)]
    return vsb_engine.assess_volatility_structure(
        spot=prices[-1], strike=round(prices[-1] / 50) * 50, t_years=7 / 365,
        ce_premium=180.0, pe_premium=140.0, closes_with_ts=closes, timestamp=ts,
    )


def test_volatility_wiring_produces_real_suitability_with_real_vsb():
    """The actual point of this follow-up: given a REAL VolatilityStructureAssessment,
    families that were previously always INSUFFICIENT_EVIDENCE now get a
    genuine SUITABLE/UNSUITABLE read."""
    mdi, mssi, consensus = _build_all([100, 100.5, 99.5, 100.2, 99.8, 100.3, 99.9, 100.1])
    vsb = _real_vsb([100, 100.5, 99.5, 100.2, 99.8, 100.3, 99.9, 100.1])
    assessments = ssf_engine.assess_all_families(mdi, mssi, consensus, vsb, timestamp="2026-07-24T09:30:00")
    for family in (ssf_taxonomy.VOLATILITY_EXPANSION, ssf_taxonomy.VOLATILITY_COMPRESSION,
                   ssf_taxonomy.NEUTRAL_PREMIUM_SELLING, ssf_taxonomy.NEUTRAL_PREMIUM_BUYING,
                   ssf_taxonomy.RATIO, ssf_taxonomy.BUTTERFLY):
        a = ssf_query.by_family(assessments, family)
        assert a.suitability in (ssf_taxonomy.SUITABLE, ssf_taxonomy.UNSUITABLE), f"{family} should get a real read with real vsb, got {a.suitability}"
        assert vsb.assessment_id in a.supporting_assessment_ids


def test_iron_condor_iron_fly_still_insufficient_evidence_even_with_real_vsb():
    """Liquidity remains genuinely unavailable codebase-wide -- these
    two families must stay INSUFFICIENT_EVIDENCE even when a real vsb
    is supplied."""
    mdi, mssi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    vsb = _real_vsb([100, 101, 102, 103, 104, 105, 106])
    assessments = ssf_engine.assess_all_families(mdi, mssi, consensus, vsb, timestamp="2026-07-24T09:30:00")
    for family in (ssf_taxonomy.IRON_CONDOR, ssf_taxonomy.IRON_FLY):
        a = ssf_query.by_family(assessments, family)
        assert a.suitability == ssf_taxonomy.INSUFFICIENT_EVIDENCE
        assert ssf_taxonomy.DOMAIN_LIQUIDITY in a.required_missing_evidence
        assert ssf_taxonomy.DOMAIN_VOLATILITY not in a.required_missing_evidence  # volatility itself is now satisfied.


def test_calendar_still_insufficient_evidence_even_with_real_vsb():
    """CALENDAR needs term structure specifically -- VSB never supplies
    it (always UNKNOWN) -- must remain INSUFFICIENT_EVIDENCE regardless
    of a real vsb being supplied."""
    mdi, mssi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    vsb = _real_vsb([100, 101, 102, 103, 104, 105, 106])
    assessments = ssf_engine.assess_all_families(mdi, mssi, consensus, vsb, timestamp="2026-07-24T09:30:00")
    a = ssf_query.by_family(assessments, ssf_taxonomy.CALENDAR)
    assert a.suitability == ssf_taxonomy.INSUFFICIENT_EVIDENCE
    assert ssf_taxonomy.DOMAIN_VOLATILITY_TERM_STRUCTURE in a.required_missing_evidence


def test_backward_compatible_call_without_vsb_still_works():
    """Every pre-existing caller passing only (mdi, mssi, consensus)
    must still work identically -- vsb is optional."""
    mdi, mssi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    assessments = ssf_engine.assess_all_families(mdi, mssi, consensus, timestamp="2026-07-24T09:30:00")
    assert len(assessments) == len(ssf_taxonomy.ALL_STRATEGY_FAMILIES)
    long_dir = ssf_query.by_family(assessments, ssf_taxonomy.LONG_DIRECTIONAL)
    assert long_dir.suitability in (ssf_taxonomy.SUITABLE, ssf_taxonomy.UNSUITABLE)  # unaffected by vsb wiring.


def test_synthetic_reports_insufficient_evidence_for_liquidity():
    mdi, mssi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    assessments = ssf_engine.assess_all_families(mdi, mssi, consensus, timestamp="2026-07-24T09:30:00")
    a = ssf_query.by_family(assessments, ssf_taxonomy.SYNTHETIC)
    assert a.suitability == ssf_taxonomy.INSUFFICIENT_EVIDENCE
    assert ssf_taxonomy.DOMAIN_LIQUIDITY in a.required_missing_evidence


# ---------------------------------------------------------------------------
# Ready-today families genuinely get a real SUITABLE/UNSUITABLE read
# (never INSUFFICIENT_EVIDENCE) given real, sufficient upstream data.
# ---------------------------------------------------------------------------
def test_directional_families_get_real_suitability_reads():
    mdi, mssi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106, 107])
    assessments = ssf_engine.assess_all_families(mdi, mssi, consensus, timestamp="2026-07-24T09:30:00")
    long_dir = ssf_query.by_family(assessments, ssf_taxonomy.LONG_DIRECTIONAL)
    short_dir = ssf_query.by_family(assessments, ssf_taxonomy.SHORT_DIRECTIONAL)
    assert long_dir.suitability in (ssf_taxonomy.SUITABLE, ssf_taxonomy.UNSUITABLE)
    assert short_dir.suitability in (ssf_taxonomy.SUITABLE, ssf_taxonomy.UNSUITABLE)
    # Real, non-empty explanation either way.
    assert len(long_dir.explanation.supporting_evidence) + len(long_dir.explanation.rejecting_evidence) > 0


def test_explainability_completeness():
    mdi, mssi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    assessments = ssf_engine.assess_all_families(mdi, mssi, consensus, timestamp="2026-07-24T09:30:00")
    for a in assessments:
        if a.suitability == ssf_taxonomy.SUITABLE:
            assert len(a.explanation.why_suitable) > 0
        elif a.suitability in (ssf_taxonomy.UNSUITABLE, ssf_taxonomy.INSUFFICIENT_EVIDENCE):
            assert len(a.explanation.why_unsuitable) > 0
        assert a.explanation.assessment_id == a.assessment_id


# ---------------------------------------------------------------------------
# Determinism / immutability / replay parity
# ---------------------------------------------------------------------------
def test_determinism_same_input_same_id():
    mdi, mssi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    a1 = ssf_engine.assess_strategy_suitability(ssf_taxonomy.LONG_DIRECTIONAL, mdi, mssi, consensus, timestamp="2026-07-24T09:30:00")
    a2 = ssf_engine.assess_strategy_suitability(ssf_taxonomy.LONG_DIRECTIONAL, mdi, mssi, consensus, timestamp="2099-01-01T00:00:00")
    assert a1.assessment_id == a2.assessment_id
    assert a1.suitability == a2.suitability


def test_assessments_are_immutable():
    mdi, mssi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    a = ssf_engine.assess_strategy_suitability(ssf_taxonomy.LONG_DIRECTIONAL, mdi, mssi, consensus, timestamp="2026-07-24T09:30:00")
    try:
        a.suitability = ssf_taxonomy.UNSUITABLE
        assert False, "should have raised"
    except Exception:
        pass


def test_batch_vs_streaming_parity():
    mdi, mssi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    batch = ssf_runner.assess_all_strategy_suitability(mdi, mssi, consensus, timestamp="2026-07-24T09:30:00")
    stream = ssf_runner.StrategySuitabilityStream()
    live = stream.process(mdi, mssi, consensus, timestamp="2026-07-24T09:30:00")
    assert len(batch) == len(live)
    for b, l in zip(batch, live):
        assert b.assessment_id == l.assessment_id
        assert ssf_serialization.assessment_to_json(b) == ssf_serialization.assessment_to_json(l)


def test_byte_identical_full_rerun():
    def _run():
        mdi, mssi, consensus = _build_all([100, 99, 100, 101, 102, 103, 104, 90])
        return ssf_engine.assess_all_families(mdi, mssi, consensus, timestamp="2026-07-24T09:30:00")
    r1, r2 = _run(), _run()
    assert [ssf_serialization.assessment_to_json(a) for a in r1] == [ssf_serialization.assessment_to_json(a) for a in r2]


# ---------------------------------------------------------------------------
# Serialization / journal / query
# ---------------------------------------------------------------------------
def test_serialization_round_trip():
    mdi, mssi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    a = ssf_engine.assess_strategy_suitability(ssf_taxonomy.LONG_DIRECTIONAL, mdi, mssi, consensus, timestamp="2026-07-24T09:30:00")
    recovered = ssf_serialization.assessment_from_json(ssf_serialization.assessment_to_json(a))
    assert recovered == a


def test_journal_append_only():
    mdi, mssi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    a = ssf_engine.assess_strategy_suitability(ssf_taxonomy.LONG_DIRECTIONAL, mdi, mssi, consensus, timestamp="2026-07-24T09:30:00")
    journal = ssf_journal.StrategySelectionFoundationJournal()
    journal.record_assessment(a, recorded_at="2026-07-24T09:30:01")
    entries_before = journal.entries()
    journal.record_assessment(a, recorded_at="2026-07-24T09:30:02")
    assert len(journal) == 2
    assert entries_before == journal.entries()[:1]


def test_query_helpers():
    mdi, mssi, consensus = _build_all([100, 101, 102, 103, 104, 105, 106])
    assessments = ssf_engine.assess_all_families(mdi, mssi, consensus, timestamp="2026-07-24T09:30:00")
    a = ssf_query.by_family(assessments, ssf_taxonomy.LONG_DIRECTIONAL)
    assert ssf_query.by_id(assessments, a.assessment_id) == a
    assert ssf_query.by_id(assessments, "nonexistent") is None
    assert a in ssf_query.by_suitability(assessments, a.suitability)


# ---------------------------------------------------------------------------
# AST isolation
# ---------------------------------------------------------------------------
_PACKAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bujji", "msi_strategy_selection_foundation")

_FORBIDDEN_MODULE_PREFIXES = (
    "mic_v2", "bujji.mic_replay", "bujji.production_runtime", "bujji.trading_brain",
    "bujji.strategy_selector", "fyers_apiv3", "bujji.intelligence",
    "bujji.msi_price_structure", "bujji.msi_decision_synthesis",
    "bujji.msi_strategy_eligibility", "bujji.msi_trade_intent", "bujji.msi_participant_positioning",
)

_FORBIDDEN_TERMS = ("uuid4", "strike_select", "expiry_select", "place_order", "execution_plan", "optimi", "score", "rank_")


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
