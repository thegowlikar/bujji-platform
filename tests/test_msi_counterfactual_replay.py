"""Tests for bujji.msi_counterfactual_replay -- Series 102.

Covers: causality validator, legality validator, session assembly
(unit tests, no real corpus needed), and golden replay / determinism
tests against the real corpus (mirrors the existing real-fixture
convention already established in tests/test_live_shadow_validation.py:
depends on /tmp/nifty_intraday_by_day_expanded.json and
/tmp/m1/BhavCopy_*.csv, present throughout this project's Sprint
115-122 investigation arc)."""
from __future__ import annotations

import json

import pytest

from bujji.msi_counterfactual_replay import engine as cre_engine
from bujji.msi_counterfactual_replay import query as cre_query
from bujji.msi_counterfactual_replay import replay as cre_replay
from bujji.msi_counterfactual_replay import serialization as cre_serialization
from bujji.msi_counterfactual_replay import taxonomy as cre_taxonomy
from bujji.msi_counterfactual_replay.journal import CounterfactualReplayJournal
from bujji.msi_counterfactual_replay.models import ExploredPath

DAY = "2026-07-13"  # the corpus's one real NO_TRADE day (Sprint 116 finding)
D = "20260713"


def _mk_path(label, ts, data_ts, thesis="NO_TRADE", family=None):
    return ExploredPath(
        path_id=f"{label}-{ts}", label=label, decision_timestamp=ts,
        thesis_type=thesis, selected_family=family, data_timestamps_used=tuple(data_ts),
        rationale=("test fixture",), schema_version="1.0.0",
    )


# --- Deliverable 4: causality validator ---

def test_causality_passes_when_all_data_before_decision():
    p = _mk_path("BASELINE", "2026-07-13T15:15:00", ["2026-07-13T09:15:00", "2026-07-13T15:15:00"])
    ok, reasons = cre_engine.validate_causality(p)
    assert ok is True


def test_causality_fails_when_future_data_used():
    p = _mk_path("BASELINE", "2026-07-13T12:00:00", ["2026-07-13T09:15:00", "2026-07-13T15:15:00"])
    ok, reasons = cre_engine.validate_causality(p)
    assert ok is False
    assert any("future data used" in r for r in reasons)


def test_causality_violation_names_the_exact_offending_timestamp():
    p = _mk_path("ALTERNATIVE", "2026-07-13T10:00:00", ["2026-07-13T09:15:00", "2026-07-13T14:00:00"])
    ok, reasons = cre_engine.validate_causality(p)
    assert any("2026-07-13T14:00:00" in r for r in reasons)


# --- Deliverable 3: legality validator (Phase 1 legal decision space) ---

def test_legality_passes_with_exactly_one_baseline_and_one_alternative():
    baseline = _mk_path(cre_taxonomy.PATH_LABEL_BASELINE, "2026-07-13T15:15:00", ["2026-07-13T09:15:00"])
    alt = _mk_path(cre_taxonomy.PATH_LABEL_ALTERNATIVE, "2026-07-13T12:00:00", ["2026-07-13T09:15:00"])
    ok, reasons = cre_engine.validate_legality((baseline, alt))
    assert ok is True


def test_legality_fails_with_no_exhaustive_search_more_than_two_paths():
    baseline = _mk_path(cre_taxonomy.PATH_LABEL_BASELINE, "2026-07-13T15:15:00", [])
    alt1 = _mk_path(cre_taxonomy.PATH_LABEL_ALTERNATIVE, "2026-07-13T12:00:00", [])
    alt2 = _mk_path(cre_taxonomy.PATH_LABEL_ALTERNATIVE, "2026-07-13T13:00:00", [])
    ok, reasons = cre_engine.validate_legality((baseline, alt1, alt2))
    assert ok is False
    assert any("exactly NO_TRADE vs ONE alternative" in r for r in reasons)


def test_legality_fails_with_two_baselines_no_alternative():
    b1 = _mk_path(cre_taxonomy.PATH_LABEL_BASELINE, "2026-07-13T15:15:00", [])
    b2 = _mk_path(cre_taxonomy.PATH_LABEL_BASELINE, "2026-07-13T12:00:00", [])
    ok, reasons = cre_engine.validate_legality((b1, b2))
    assert ok is False


# --- Deliverable 1/2: CounterfactualSession assembly ---

def test_session_is_legal_when_both_validators_pass():
    baseline = _mk_path(cre_taxonomy.PATH_LABEL_BASELINE, "2026-07-13T15:15:00", ["2026-07-13T09:15:00", "2026-07-13T15:15:00"])
    alt = _mk_path(cre_taxonomy.PATH_LABEL_ALTERNATIVE, "2026-07-13T12:00:00", ["2026-07-13T09:15:00", "2026-07-13T12:00:00"])
    session = cre_engine.build_counterfactual_session(
        replay_id="r1", baseline_path=baseline, alternative_path=alt,
        production_version="test-version", replay_version="1.0.0", supporting_references=["decision:d1"],
    )
    assert session.replay_legality == cre_taxonomy.LEGALITY_LEGAL


def test_session_is_illegal_and_still_recorded_when_causality_fails():
    """A causality violation must be DISCLOSED, not hidden or silently
    dropped -- build_counterfactual_session still returns a real session,
    just marked ILLEGAL with honest reasoning."""
    baseline = _mk_path(cre_taxonomy.PATH_LABEL_BASELINE, "2026-07-13T12:00:00", ["2026-07-13T15:15:00"])  # future data
    alt = _mk_path(cre_taxonomy.PATH_LABEL_ALTERNATIVE, "2026-07-13T11:00:00", ["2026-07-13T09:15:00"])
    session = cre_engine.build_counterfactual_session(
        replay_id="r2", baseline_path=baseline, alternative_path=alt,
        production_version="test-version", replay_version="1.0.0", supporting_references=[],
    )
    assert session.replay_legality == cre_taxonomy.LEGALITY_ILLEGAL
    assert any("future data used" in v for v in session.causality_verdicts)


def test_session_earliest_causal_timestamp_is_the_real_minimum():
    baseline = _mk_path(cre_taxonomy.PATH_LABEL_BASELINE, "2026-07-13T15:15:00", ["2026-07-13T10:00:00"])
    alt = _mk_path(cre_taxonomy.PATH_LABEL_ALTERNATIVE, "2026-07-13T12:00:00", ["2026-07-13T09:15:00"])
    session = cre_engine.build_counterfactual_session(
        replay_id="r3", baseline_path=baseline, alternative_path=alt,
        production_version="v", replay_version="1.0.0", supporting_references=[],
    )
    assert session.earliest_causal_timestamp == "2026-07-13T09:15:00"


def test_session_deterministic_id_same_inputs_same_id():
    baseline = _mk_path(cre_taxonomy.PATH_LABEL_BASELINE, "2026-07-13T15:15:00", ["2026-07-13T09:15:00"])
    alt = _mk_path(cre_taxonomy.PATH_LABEL_ALTERNATIVE, "2026-07-13T12:00:00", ["2026-07-13T09:15:00"])
    s1 = cre_engine.build_counterfactual_session(
        replay_id="r4", baseline_path=baseline, alternative_path=alt,
        production_version="v", replay_version="1.0.0", supporting_references=["x"],
    )
    s2 = cre_engine.build_counterfactual_session(
        replay_id="r4", baseline_path=baseline, alternative_path=alt,
        production_version="v", replay_version="1.0.0", supporting_references=["x"],
    )
    assert s1.session_id == s2.session_id
    assert s1 == s2


def test_rejected_paths_always_discloses_phase1_scope_limit():
    baseline = _mk_path(cre_taxonomy.PATH_LABEL_BASELINE, "2026-07-13T15:15:00", [])
    alt = _mk_path(cre_taxonomy.PATH_LABEL_ALTERNATIVE, "2026-07-13T12:00:00", [])
    session = cre_engine.build_counterfactual_session(
        replay_id="r5", baseline_path=baseline, alternative_path=alt,
        production_version="v", replay_version="1.0.0", supporting_references=[],
    )
    assert any("out of Phase 1 scope" in r for r in session.rejected_paths)


# --- Journal / serialization / query ---

def test_journal_round_trip(tmp_path):
    baseline = _mk_path(cre_taxonomy.PATH_LABEL_BASELINE, "2026-07-13T15:15:00", ["2026-07-13T09:15:00"])
    alt = _mk_path(cre_taxonomy.PATH_LABEL_ALTERNATIVE, "2026-07-13T12:00:00", ["2026-07-13T09:15:00"])
    session = cre_engine.build_counterfactual_session(
        replay_id="r6", baseline_path=baseline, alternative_path=alt,
        production_version="v", replay_version="1.0.0", supporting_references=[],
    )
    journal = CounterfactualReplayJournal(tmp_path)
    journal.record(session)
    assert journal.by_id(session.session_id) == session


def test_query_legal_vs_illegal_sessions():
    baseline_ok = _mk_path(cre_taxonomy.PATH_LABEL_BASELINE, "2026-07-13T15:15:00", ["2026-07-13T09:15:00"])
    alt_ok = _mk_path(cre_taxonomy.PATH_LABEL_ALTERNATIVE, "2026-07-13T12:00:00", ["2026-07-13T09:15:00"])
    legal = cre_engine.build_counterfactual_session(
        replay_id="r7", baseline_path=baseline_ok, alternative_path=alt_ok,
        production_version="v", replay_version="1.0.0", supporting_references=[],
    )
    baseline_bad = _mk_path(cre_taxonomy.PATH_LABEL_BASELINE, "2026-07-13T10:00:00", ["2026-07-13T15:15:00"])
    illegal = cre_engine.build_counterfactual_session(
        replay_id="r8", baseline_path=baseline_bad, alternative_path=alt_ok,
        production_version="v", replay_version="1.0.0", supporting_references=[],
    )
    sessions = [legal, illegal]
    assert cre_query.legal_sessions(sessions) == (legal,)
    assert cre_query.illegal_sessions(sessions) == (illegal,)


def test_serialization_round_trip():
    baseline = _mk_path(cre_taxonomy.PATH_LABEL_BASELINE, "2026-07-13T15:15:00", ["2026-07-13T09:15:00"])
    alt = _mk_path(cre_taxonomy.PATH_LABEL_ALTERNATIVE, "2026-07-13T12:00:00", ["2026-07-13T09:15:00"])
    session = cre_engine.build_counterfactual_session(
        replay_id="r9", baseline_path=baseline, alternative_path=alt,
        production_version="v", replay_version="1.0.0", supporting_references=["d1"],
    )
    text = cre_serialization.session_to_json(session)
    recovered = cre_serialization.session_from_json(text)
    assert recovered == session


# --- Deliverable 8/9: Golden replay tests + determinism, real corpus ---

def _load_real_day():
    with open("/tmp/nifty_intraday_by_day_expanded.json") as f:
        candles = json.load(f)[DAY]
    with open(f"/tmp/m1/BhavCopy_NSE_FO_0_0_0_{D}_F_0000.csv") as f:
        bhav_text = f.read()
    return candles, bhav_text


def test_golden_replay_baseline_matches_real_production_no_trade_day():
    """The real corpus's one NO_TRADE day (Sprint 116 finding): replaying
    the FULL real day through CRE's real_path function must reproduce
    the real, already-known NO_TRADE outcome -- not a fabricated one."""
    candles, bhav_text = _load_real_day()
    baseline = cre_replay.run_real_path(
        cre_taxonomy.PATH_LABEL_BASELINE, DAY, bhav_text, candles, candles[-1]["ts"], "data/test_cre_golden1.lock",
    )
    assert baseline.thesis_type == "NO_TRADE"
    assert baseline.selected_family is None


def test_golden_replay_alternative_is_causally_truncated_and_legal():
    candles, bhav_text = _load_real_day()
    mid_ts = candles[len(candles) // 2]["ts"]
    baseline = cre_replay.run_real_path(
        cre_taxonomy.PATH_LABEL_BASELINE, DAY, bhav_text, candles, candles[-1]["ts"], "data/test_cre_golden2a.lock",
    )
    alternative = cre_replay.run_real_path(
        cre_taxonomy.PATH_LABEL_ALTERNATIVE, DAY, bhav_text, candles, mid_ts, "data/test_cre_golden2b.lock",
    )
    session = cre_engine.build_counterfactual_session(
        replay_id="golden-2", baseline_path=baseline, alternative_path=alternative,
        production_version="test", replay_version="1.0.0", supporting_references=[],
    )
    assert session.replay_legality == cre_taxonomy.LEGALITY_LEGAL
    assert max(alternative.data_timestamps_used) <= mid_ts
    assert alternative.decision_timestamp <= baseline.decision_timestamp


def test_replay_determinism_same_real_inputs_produce_byte_identical_path():
    candles, bhav_text = _load_real_day()
    p1 = cre_replay.run_real_path(
        cre_taxonomy.PATH_LABEL_BASELINE, DAY, bhav_text, candles, candles[-1]["ts"], "data/test_cre_det1.lock",
    )
    p2 = cre_replay.run_real_path(
        cre_taxonomy.PATH_LABEL_BASELINE, DAY, bhav_text, candles, candles[-1]["ts"], "data/test_cre_det2.lock",
    )
    assert p1.thesis_type == p2.thesis_type
    assert p1.selected_family == p2.selected_family
    assert p1.data_timestamps_used == p2.data_timestamps_used


def test_run_real_path_raises_on_impossible_cutoff_before_any_real_data():
    candles, bhav_text = _load_real_day()
    with pytest.raises(ValueError):
        cre_replay.run_real_path(
            cre_taxonomy.PATH_LABEL_ALTERNATIVE, DAY, bhav_text, candles, "2020-01-01T00:00:00", "data/test_cre_impossible.lock",
        )


def test_golden_replay_production_state_unaffected_by_cre():
    """CRE construction has zero effect on Production's own real
    DecisionRecord-building -- same golden-replay guarantee already
    proven for MLE (Series 100) and EPS (Series 101)."""
    from bujji.msi_decision_auditor import engine as da_engine
    from bujji.msi_trade_thesis.models import Explanation as ThesisExplanation, TradeThesisAssessment

    thesis = TradeThesisAssessment(
        assessment_id="thesis-cre-golden-1", timestamp="2026-07-13T15:15:00", thesis_type="NO_TRADE",
        market_expectation="no edge", expected_move=None, expected_time_horizon="INTRADAY",
        volatility_expectation="STABLE", directional_expectation="UNKNOWN", conviction="NONE",
        invalidation_conditions=(), supporting_domains=(), conflicting_domains=(),
        explanation=ThesisExplanation(
            assessment_id="thesis-cre-golden-1", why_this_thesis=("real thesis",),
            supporting_evidence=(), conflicting_evidence=(), what_would_invalidate=(),
            schema_version="1.0.0",
        ),
        provenance="test", schema_version="1.0.0",
    )
    before = da_engine.build_decision_record(
        DAY, (), (), "UNKNOWN", "UNANIMOUS_CONSENSUS", "STABLE",
        thesis, None, None, None, None, None, None, timestamp="2026-07-13T15:15:00",
    )
    candles, bhav_text = _load_real_day()
    cre_replay.run_real_path(
        cre_taxonomy.PATH_LABEL_BASELINE, DAY, bhav_text, candles, candles[-1]["ts"], "data/test_cre_golden3.lock",
    )
    after = da_engine.build_decision_record(
        DAY, (), (), "UNKNOWN", "UNANIMOUS_CONSENSUS", "STABLE",
        thesis, None, None, None, None, None, None, timestamp="2026-07-13T15:15:00",
    )
    assert before == after
