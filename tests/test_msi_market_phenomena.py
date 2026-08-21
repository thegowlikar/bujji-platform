"""Tests for bujji.msi_market_phenomena -- Series 103.

Covers: causal ordering, per-phenomenon rules (unit, synthetic-but-real-
shaped snapshots), report assembly, journal/serialization/query, and
golden replay / determinism tests against the real corpus."""
from __future__ import annotations

import json

import pytest

from bujji.msi_market_phenomena import engine as mpc_engine
from bujji.msi_market_phenomena import query as mpc_query
from bujji.msi_market_phenomena import serialization as mpc_serialization
from bujji.msi_market_phenomena import taxonomy as mpc_taxonomy
from bujji.msi_market_phenomena.journal import MarketPhenomenaJournal
from bujji.msi_market_phenomena.models import MarketSnapshot

DAY = "2026-07-20"
D = "20260720"


def _mk_snapshot(ts="2026-07-20T15:15:00", structure_state="BALANCE", compression_state="NOT_DETECTED",
                  expansion_state="NOT_DETECTED", structural_balance="UNBOUNDED", structure_location="NEAR_RESISTANCE",
                  volatility_regime="STABLE", vsb_expansion="NOT_DETECTED", vsb_compression="NOT_DETECTED",
                  overall_direction="NEUTRAL", overall_confidence="MODERATE", open_price=None, previous_close=None):
    return MarketSnapshot(
        timestamp=ts, structure_state=structure_state, compression_state=compression_state,
        expansion_state=expansion_state, structural_balance=structural_balance, structure_location=structure_location,
        volatility_regime=volatility_regime, vsb_expansion_state=vsb_expansion, vsb_compression_state=vsb_compression,
        overall_direction=overall_direction, overall_confidence=overall_confidence,
        open_price=open_price, previous_close_price=previous_close, supporting_assessment_ids=("psi-1", "mssi-1"),
    )


# --- Deliverable: causality ---

def test_causal_order_passes_for_chronological_snapshots():
    s1 = _mk_snapshot(ts="2026-07-20T09:15:00")
    s2 = _mk_snapshot(ts="2026-07-20T10:00:00")
    ok, reasons = mpc_engine.validate_causal_order([s1, s2])
    assert ok is True


def test_causal_order_fails_for_out_of_order_snapshots():
    s1 = _mk_snapshot(ts="2026-07-20T10:00:00")
    s2 = _mk_snapshot(ts="2026-07-20T09:15:00")
    ok, reasons = mpc_engine.validate_causal_order([s1, s2])
    assert ok is False


def test_classify_day_raises_on_noncausal_snapshot_sequence():
    s1 = _mk_snapshot(ts="2026-07-20T10:00:00")
    s2 = _mk_snapshot(ts="2026-07-20T09:15:00")
    with pytest.raises(ValueError):
        mpc_engine.classify_day(DAY, [s1, s2], generated_timestamp="2026-07-20T15:15:00")


# --- Per-phenomenon rules (Deliverable 1/2), real declarative conditions ---

def test_trend_expansion_requires_both_trending_and_confirmed_vsb_expansion():
    matched_snap = _mk_snapshot(structure_state="TRENDING", vsb_expansion="CONFIRMED")
    report = mpc_engine.classify_day(DAY, [matched_snap], generated_timestamp="x")
    types = {p.phenomenon_type for p in report.phenomena}
    assert mpc_taxonomy.PHENOMENON_TREND_EXPANSION in types

    unmatched_snap = _mk_snapshot(structure_state="TRENDING", vsb_expansion="NOT_DETECTED")
    report2 = mpc_engine.classify_day(DAY, [unmatched_snap], generated_timestamp="x")
    assert mpc_taxonomy.PHENOMENON_TREND_EXPANSION not in {p.phenomenon_type for p in report2.phenomena}


def test_trend_failure_matches_correcting_structure():
    s = _mk_snapshot(structure_state="CORRECTING")
    report = mpc_engine.classify_day(DAY, [s], generated_timestamp="x")
    assert mpc_taxonomy.PHENOMENON_TREND_FAILURE in {p.phenomenon_type for p in report.phenomena}


def test_range_compression_matches_confirmed_compression_or_range_bound():
    s1 = _mk_snapshot(compression_state="CONFIRMED")
    report1 = mpc_engine.classify_day(DAY, [s1], generated_timestamp="x")
    assert mpc_taxonomy.PHENOMENON_RANGE_COMPRESSION in {p.phenomenon_type for p in report1.phenomena}

    s2 = _mk_snapshot(compression_state="NOT_DETECTED", structural_balance="RANGE_BOUND")
    report2 = mpc_engine.classify_day(DAY, [s2], generated_timestamp="x")
    assert mpc_taxonomy.PHENOMENON_RANGE_COMPRESSION in {p.phenomenon_type for p in report2.phenomena}


def test_mean_reversion_requires_range_bound_and_balance():
    s = _mk_snapshot(structural_balance="RANGE_BOUND", structure_state="BALANCE")
    report = mpc_engine.classify_day(DAY, [s], generated_timestamp="x")
    assert mpc_taxonomy.PHENOMENON_MEAN_REVERSION in {p.phenomenon_type for p in report.phenomena}


def test_gap_continuation_requires_gap_and_agreeing_direction():
    s = _mk_snapshot(open_price=105.0, previous_close=100.0, overall_direction="BULLISH")
    report = mpc_engine.classify_day(DAY, [s], generated_timestamp="x")
    assert mpc_taxonomy.PHENOMENON_GAP_CONTINUATION in {p.phenomenon_type for p in report.phenomena}
    assert mpc_taxonomy.PHENOMENON_GAP_FAILURE not in {p.phenomenon_type for p in report.phenomena}


def test_gap_failure_requires_gap_and_disagreeing_direction():
    s = _mk_snapshot(open_price=105.0, previous_close=100.0, overall_direction="BEARISH")
    report = mpc_engine.classify_day(DAY, [s], generated_timestamp="x")
    assert mpc_taxonomy.PHENOMENON_GAP_FAILURE in {p.phenomenon_type for p in report.phenomena}


def test_no_gap_when_open_equals_previous_close():
    s = _mk_snapshot(open_price=100.0, previous_close=100.0, overall_direction="BULLISH")
    report = mpc_engine.classify_day(DAY, [s], generated_timestamp="x")
    types = {p.phenomenon_type for p in report.phenomena}
    assert mpc_taxonomy.PHENOMENON_GAP_CONTINUATION not in types
    assert mpc_taxonomy.PHENOMENON_GAP_FAILURE not in types


def test_gap_not_evaluated_without_real_price_data():
    s = _mk_snapshot(open_price=None, previous_close=None)
    report = mpc_engine.classify_day(DAY, [s], generated_timestamp="x")
    types = {p.phenomenon_type for p in report.phenomena}
    assert mpc_taxonomy.PHENOMENON_GAP_CONTINUATION not in types
    assert mpc_taxonomy.PHENOMENON_GAP_FAILURE not in types


# --- Report structure, honesty disclosure ---

def test_report_always_discloses_not_classifiable_phenomena():
    s = _mk_snapshot()
    report = mpc_engine.classify_day(DAY, [s], generated_timestamp="x")
    assert set(report.not_classifiable) == set(mpc_taxonomy.ALL_NOT_CLASSIFIABLE_V1)
    assert report.not_classifiable_reason == mpc_taxonomy.NOT_CLASSIFIABLE_REASON


def test_report_has_no_phenomena_when_no_rule_matches_never_fabricates():
    s = _mk_snapshot(structure_state="BALANCE", compression_state="EARLY", structural_balance="UNBOUNDED",
                      structure_location="NEAR_RESISTANCE", vsb_expansion="NOT_DETECTED", vsb_compression="NOT_DETECTED")
    report = mpc_engine.classify_day(DAY, [s], generated_timestamp="x")
    assert report.phenomena == ()  # honest empty result, not a forced classification


def test_phenomenon_confidence_high_when_matches_all_snapshots():
    s1 = _mk_snapshot(ts="2026-07-20T10:00:00", structure_state="CORRECTING")
    s2 = _mk_snapshot(ts="2026-07-20T11:00:00", structure_state="CORRECTING")
    report = mpc_engine.classify_day(DAY, [s1, s2], generated_timestamp="x")
    p = next(p for p in report.phenomena if p.phenomenon_type == mpc_taxonomy.PHENOMENON_TREND_FAILURE)
    assert p.confidence == mpc_taxonomy.CONFIDENCE_HIGH
    assert p.earliest_detection_timestamp == "2026-07-20T10:00:00"
    assert p.latest_confirmation_timestamp == "2026-07-20T11:00:00"


def test_phenomenon_confidence_low_when_matches_minority_of_snapshots():
    s1 = _mk_snapshot(ts="2026-07-20T10:00:00", structure_state="CORRECTING")
    s2 = _mk_snapshot(ts="2026-07-20T11:00:00", structure_state="BALANCE")
    s3 = _mk_snapshot(ts="2026-07-20T12:00:00", structure_state="BALANCE")
    report = mpc_engine.classify_day(DAY, [s1, s2, s3], generated_timestamp="x")
    p = next(p for p in report.phenomena if p.phenomenon_type == mpc_taxonomy.PHENOMENON_TREND_FAILURE)
    assert p.confidence == mpc_taxonomy.CONFIDENCE_LOW


def test_duration_seconds_computed_from_real_timestamps():
    s1 = _mk_snapshot(ts="2026-07-20T10:00:00", structure_state="CORRECTING")
    s2 = _mk_snapshot(ts="2026-07-20T11:00:00", structure_state="CORRECTING")
    report = mpc_engine.classify_day(DAY, [s1, s2], generated_timestamp="x")
    p = next(p for p in report.phenomena if p.phenomenon_type == mpc_taxonomy.PHENOMENON_TREND_FAILURE)
    assert p.duration_seconds == 3600.0


def test_evidence_references_are_real_supporting_assessment_ids():
    s = _mk_snapshot(structure_state="CORRECTING")
    report = mpc_engine.classify_day(DAY, [s], generated_timestamp="x")
    p = next(p for p in report.phenomena if p.phenomenon_type == mpc_taxonomy.PHENOMENON_TREND_FAILURE)
    assert p.evidence_references == ("mssi-1", "psi-1")


# --- Determinism ---

def test_classify_day_is_deterministic():
    s = _mk_snapshot(structure_state="CORRECTING")
    r1 = mpc_engine.classify_day(DAY, [s], generated_timestamp="x")
    r2 = mpc_engine.classify_day(DAY, [s], generated_timestamp="x")
    assert mpc_serialization.report_to_dict(r1) == mpc_serialization.report_to_dict(r2)


# --- Journal / serialization / query ---

def test_journal_round_trip(tmp_path):
    s = _mk_snapshot(structure_state="CORRECTING")
    report = mpc_engine.classify_day(DAY, [s], generated_timestamp="x")
    journal = MarketPhenomenaJournal(tmp_path)
    journal.record(report)
    assert journal.by_day(DAY) == report


def test_serialization_round_trip():
    s = _mk_snapshot(structure_state="CORRECTING")
    report = mpc_engine.classify_day(DAY, [s], generated_timestamp="x")
    text = mpc_serialization.report_to_json(report)
    recovered = mpc_serialization.report_from_json(text)
    assert recovered == report


def test_query_phenomena_of_type_and_days_with_phenomenon():
    s = _mk_snapshot(structure_state="CORRECTING")
    report = mpc_engine.classify_day(DAY, [s], generated_timestamp="x")
    found = mpc_query.phenomena_of_type(report, mpc_taxonomy.PHENOMENON_TREND_FAILURE)
    assert len(found) == 1
    assert mpc_query.days_with_phenomenon([report], mpc_taxonomy.PHENOMENON_TREND_FAILURE) == (DAY,)
    assert mpc_query.days_with_phenomenon([report], mpc_taxonomy.PHENOMENON_MEAN_REVERSION) == ()


# --- Deliverable 6/7: Replay compatibility + Golden replay tests, real corpus ---

def _load_real_day(day, d):
    with open("/tmp/nifty_intraday_by_day_expanded.json") as f:
        candles = json.load(f)[day]
    with open(f"/tmp/m1/BhavCopy_NSE_FO_0_0_0_{d}_F_0000.csv") as f:
        bhav_text = f.read()
    return candles, bhav_text


def test_golden_replay_classifies_a_real_correcting_day():
    """2026-05-29 real corpus day: confirmed via manual verification to
    produce real TREND_FAILURE + VOLATILITY_EXPANSION under this
    package's real, unmodified rules."""
    from bujji.live_pipeline_bridge import SessionDriver
    from bujji.market_observation import engine as moc_engine, taxonomy as moc_taxonomy
    from bujji.live_market_events import engine as lme_engine
    from bujji.market_episode import engine as mee_engine
    from bujji.msi_market_phenomena import translate as mpc_translate

    DAY2, D2 = "2026-05-29", "20260529"
    candles, bhav_text = _load_real_day(DAY2, D2)

    def _mk_observation(timestamp, price):
        return moc_engine.build_observation(
            observation_type=moc_taxonomy.ALL_OBSERVATION_TYPES[0], instrument="NIFTY", exchange="NSE", segment="EQ",
            timestamp=timestamp, resolution=moc_taxonomy.RESOLUTION_FIFTEEN_MINUTE, source="FYERS_REAL_INTRADAY",
            schema_version="1.0.0", value_kind=moc_taxonomy.VALUE_KIND_SCALAR, payload=price,
            completeness=1.0, freshness=0.0, confidence=1.0, missing_fields=(),
            validation_status=moc_taxonomy.VALIDATION_VALID, source_quality="HIGH",
            originating_source="FYERS_REAL_INTRADAY", acquisition_timestamp=timestamp,
            normalization_timestamp=timestamp, origin=moc_taxonomy.ORIGIN_HISTORICAL_RECONSTRUCTION, provenance_version="1.0.0",
        )

    driver = SessionDriver(lock_path="data/test_mpc_golden1.lock")
    driver.acquire()
    try:
        driver.load_option_chain(bhav_text, DAY2)
        observations = [_mk_observation(c["ts"], c["close"]) for c in candles]
        previous = None
        for obs in observations:
            driver.result.observations.append(obs)
            for ev in lme_engine.detect_price_change(obs, previous):
                driver.result.events.append(ev)
                driver.result.episodes = mee_engine.advance_time(driver.result.episodes, ev.timestamp, detection_context="REPLAY")
                driver.result.episodes = mee_engine.process_event(driver.result.episodes, ev, detection_context="REPLAY")
            previous = obs
        driver._today_closes = [(c["ts"], c["close"]) for c in candles]
        last_ts = candles[-1]["ts"]
        driver.run_decision_cadence(timestamp=last_ts)
        snaps = mpc_translate.snapshots_from_real_session(driver, open_price=candles[0]["close"], previous_close_price=None)
        report = mpc_engine.classify_day(DAY2, snaps, generated_timestamp=last_ts)
        types = {p.phenomenon_type for p in report.phenomena}
        assert mpc_taxonomy.PHENOMENON_TREND_FAILURE in types
        assert mpc_taxonomy.PHENOMENON_VOLATILITY_EXPANSION in types
    finally:
        driver.release()


def test_golden_replay_production_state_unaffected_by_mpc():
    """Same zero-effect-on-Production guarantee already proven for MLE,
    EPS, and CRE."""
    from bujji.msi_decision_auditor import engine as da_engine
    from bujji.msi_trade_thesis.models import Explanation as ThesisExplanation, TradeThesisAssessment

    thesis = TradeThesisAssessment(
        assessment_id="thesis-mpc-golden-1", timestamp="2026-07-20T15:15:00", thesis_type="RANGE_PERSISTENCE",
        market_expectation="range-bound", expected_move=None, expected_time_horizon="INTRADAY",
        volatility_expectation="STABLE", directional_expectation="NEUTRAL", conviction="HIGH",
        invalidation_conditions=(), supporting_domains=(), conflicting_domains=(),
        explanation=ThesisExplanation(
            assessment_id="thesis-mpc-golden-1", why_this_thesis=("real thesis",),
            supporting_evidence=(), conflicting_evidence=(), what_would_invalidate=(),
            schema_version="1.0.0",
        ),
        provenance="test", schema_version="1.0.0",
    )
    before = da_engine.build_decision_record(
        DAY, (), (), "NEUTRAL", "UNANIMOUS_CONSENSUS", "STABLE",
        thesis, None, None, None, None, None, None, timestamp="2026-07-20T15:15:00",
    )
    s = _mk_snapshot(structure_state="CORRECTING")
    mpc_engine.classify_day(DAY, [s], generated_timestamp="x")
    after = da_engine.build_decision_record(
        DAY, (), (), "NEUTRAL", "UNANIMOUS_CONSENSUS", "STABLE",
        thesis, None, None, None, None, None, None, timestamp="2026-07-20T15:15:00",
    )
    assert before == after
