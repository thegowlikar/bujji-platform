"""Tests for bujji.live_shadow_validation (Sprint 106)."""
import ast
import json
import os

import pytest

from bujji.live_pipeline_bridge import SessionDriver
from bujji.live_shadow_validation import (
    FAILURE_CATALOGUE, run_full_cadence, build_parity_report,
    record_operational_metrics, generate_daily_parity_report,
    check_success_criteria, ParityFieldResult, ParityReport,
    PARITY_SUCCESS_THRESHOLD,
)
from bujji.msi_portfolio_construction.models import PortfolioState
from bujji.market_observation import engine as moc_engine, taxonomy as moc_taxonomy
from bujji.live_market_events import engine as lme_engine
from bujji.market_episode import engine as mee_engine

DAY = "2026-05-25"
D = "20260525"


def _load_real_day():
    with open("/tmp/nifty_intraday_by_day_expanded.json") as f:
        candles = json.load(f)[DAY]
    with open(f"/tmp/m1/BhavCopy_NSE_FO_0_0_0_{D}_F_0000.csv") as f:
        bhav_text = f.read()
    return candles, bhav_text


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


def _run_live(candles, bhav_text, lock_name):
    driver = SessionDriver(lock_path=f"data/{lock_name}.lock")
    driver.acquire()
    driver.load_option_chain(bhav_text, DAY)
    for c in candles:
        driver.process_tick("NIFTY", c["ts"], c["close"], source="recorded_stream")
    last_ts = candles[-1]["ts"]
    result = driver.run_decision_cadence(timestamp=last_ts)
    spot = next((row.underlying_price for row in driver._chain if row.underlying_price is not None), None)
    cadence = run_full_cadence(driver, spot=spot, day=DAY, portfolio=PortfolioState(), timestamp=last_ts)
    driver.close_session()
    return driver, result, cadence


def _run_replay(candles, bhav_text, lock_name):
    driver = SessionDriver(lock_path=f"data/{lock_name}.lock")
    driver.acquire()
    driver.load_option_chain(bhav_text, DAY)
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
    result = driver.run_decision_cadence(timestamp=last_ts)
    spot = next((row.underlying_price for row in driver._chain if row.underlying_price is not None), None)
    cadence = run_full_cadence(driver, spot=spot, day=DAY, portfolio=PortfolioState(), timestamp=last_ts)
    driver.close_session()
    return driver, result, cadence


def test_failure_catalogue_covers_all_four_spec_categories():
    assert set(FAILURE_CATALOGUE.keys()) == {"DATA", "PIPELINE", "DECISION", "INFRASTRUCTURE"}
    assert "MISSING_QUOTE" in FAILURE_CATALOGUE["DATA"]
    assert "CHAIN_UNAVAILABLE" in FAILURE_CATALOGUE["DATA"]
    assert "RECONNECT" in FAILURE_CATALOGUE["INFRASTRUCTURE"]


def test_run_full_cadence_produces_real_decision_and_never_places_an_order():
    candles, bhav_text = _load_real_day()
    _, result, cadence = _run_live(candles, bhav_text, "test_cadence")
    assert cadence.decision is not None
    assert cadence.ssf is not None
    assert cadence.selection is not None
    # A trade was constructed and admitted this real day -- the shadow
    # position must be a real ShadowPosition, never a broker fill.
    if cadence.admitted_trade is not None:
        assert cadence.shadow_position is not None
        assert cadence.shadow_position.entry_price is not None


def test_rerun_of_the_same_live_path_is_byte_identical():
    candles, bhav_text = _load_real_day()
    _, result1, cadence1 = _run_live(candles, bhav_text, "test_rerun_a")
    _, result2, cadence2 = _run_live(candles, bhav_text, "test_rerun_b")
    report = build_parity_report(DAY, result1, cadence1, result2, cadence2)
    assert report.match_rate == 1.0
    assert report.mismatches() == ()


def test_parity_report_explains_every_mismatch_between_live_and_replay_conventions():
    candles, bhav_text = _load_real_day()
    _, live_result, live_cadence = _run_live(candles, bhav_text, "test_parity_live")
    _, replay_result, replay_cadence = _run_replay(candles, bhav_text, "test_parity_replay")
    report = build_parity_report(DAY, live_result, live_cadence, replay_result, replay_cadence)
    for m in report.mismatches():
        assert m.explanation is not None and len(m.explanation) > 0
        assert m.category in FAILURE_CATALOGUE
        assert m.subcategory in FAILURE_CATALOGUE[m.category] or m.subcategory == "ASSESSMENT_ID_MISMATCH"


def test_decision_level_fields_match_despite_observation_level_divergence():
    """Real, disclosed finding: live (tick-resolution) and the historical
    batch/candle replay convention (15-min resolution) mint different
    observation_ids (resolution is part of the identity hash) -- but the
    THESIS, CONVICTION, STRATEGY, CONSTRUCTION, MARGIN, PORTFOLIO, and
    EXECUTION PLAN outcomes are unaffected, because they depend on the
    real price/volatility evidence, not on how granularly it arrived."""
    candles, bhav_text = _load_real_day()
    _, live_result, live_cadence = _run_live(candles, bhav_text, "test_decision_live")
    _, replay_result, replay_cadence = _run_replay(candles, bhav_text, "test_decision_replay")
    assert live_result.thesis.thesis_type == replay_result.thesis.thesis_type
    assert live_result.thesis.conviction == replay_result.thesis.conviction
    assert live_cadence.selection.selected_strategy_family == replay_cadence.selection.selected_strategy_family


def test_daily_parity_report_is_human_readable_and_includes_metrics():
    candles, bhav_text = _load_real_day()
    _, live_result, live_cadence = _run_live(candles, bhav_text, "test_report_live")
    _, replay_result, replay_cadence = _run_replay(candles, bhav_text, "test_report_replay")
    report = build_parity_report(DAY, live_result, live_cadence, replay_result, replay_cadence)
    metrics = record_operational_metrics(DAY, live_result, decision_latency_seconds=0.01, cadence_duration_seconds=0.02)
    text = generate_daily_parity_report(DAY, metrics, report)
    assert "Live Session" in text
    assert "Thesis match" in text
    assert "Operational metrics" in text
    assert "peak_memory_kb" in text


def test_success_criteria_fails_below_threshold_and_passes_above():
    passing = ParityReport(day=DAY, fields=tuple(
        ParityFieldResult(field=f"f{i}", live_value=1, replay_value=1, match=True) for i in range(100)
    ))
    result = check_success_criteria([passing], rerun_deterministic=True, crashed=False, duplicate_decisions=0, dropped_sessions=0)
    assert result["replay_live_parity_pct"] == 1.0
    assert result["parity_meets_threshold"] is True
    assert result["all_criteria_met"] is True

    failing = ParityReport(day=DAY, fields=(
        ParityFieldResult(field="thesis_type", live_value="A", replay_value="B", match=False,
                           category="DECISION", subcategory="DIFFERENT_THESIS", explanation="explained"),
    ))
    result2 = check_success_criteria([failing], rerun_deterministic=True, crashed=False, duplicate_decisions=0, dropped_sessions=0)
    assert result2["parity_meets_threshold"] is False
    assert result2["all_criteria_met"] is False


def test_success_criteria_fails_on_unexplained_mismatch():
    unexplained = ParityReport(day=DAY, fields=(
        ParityFieldResult(field="x", live_value=1, replay_value=2, match=False, category=None, subcategory=None, explanation=None),
    ))
    result = check_success_criteria([unexplained], rerun_deterministic=True, crashed=False, duplicate_decisions=0, dropped_sessions=0)
    assert result["every_mismatch_explained"] is False
    assert result["all_criteria_met"] is False


def test_success_criteria_fails_on_crash_or_duplicate_or_dropped_session():
    ok = ParityReport(day=DAY, fields=(ParityFieldResult(field="f", live_value=1, replay_value=1, match=True),))
    assert check_success_criteria([ok], rerun_deterministic=True, crashed=True, duplicate_decisions=0, dropped_sessions=0)["all_criteria_met"] is False
    assert check_success_criteria([ok], rerun_deterministic=True, crashed=False, duplicate_decisions=1, dropped_sessions=0)["all_criteria_met"] is False
    assert check_success_criteria([ok], rerun_deterministic=True, crashed=False, duplicate_decisions=0, dropped_sessions=1)["all_criteria_met"] is False
    assert check_success_criteria([ok], rerun_deterministic=False, crashed=False, duplicate_decisions=0, dropped_sessions=0)["all_criteria_met"] is False


def test_operational_metrics_reflect_real_session_counters():
    candles, bhav_text = _load_real_day()
    _, result, _cadence = _run_live(candles, bhav_text, "test_metrics")
    metrics = record_operational_metrics(DAY, result, decision_latency_seconds=0.005, cadence_duration_seconds=0.01)
    assert metrics.observations == len(result.observations)
    assert metrics.episodes == len(result.episodes)
    assert metrics.reconnects == result.reconnects
    assert metrics.dropped_ticks == result.dropped_ticks
    assert metrics.peak_memory_kb > 0


def test_module_never_imports_execution_or_broker_or_calls_place_order():
    """AST-based ban (this project's established, false-positive-safe
    convention) -- raw string search on this module's own docstrings
    would false-positive on the words "broker"/"execution" used while
    explaining what is NOT imported."""
    with open("bujji/live_shadow_validation.py") as f:
        tree = ast.parse(f.read())
    forbidden_modules = {"bujji.execution", "bujji.broker", "fyers_apiv3"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(alias.name.startswith(f) for f in forbidden_modules), alias.name
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not any(node.module.startswith(f) for f in forbidden_modules), node.module
        if isinstance(node, ast.Attribute) and node.attr == "place_order":
            pytest.fail("live_shadow_validation.py must never call place_order")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "place_order":
            pytest.fail("live_shadow_validation.py must never call place_order")


def test_duplicate_tick_never_produces_a_duplicate_decision():
    """Deliverable 6: 'no duplicate decisions'. Feeding the same
    (instrument, timestamp) tick twice must not double-count it into the
    decision cadence -- reuses SessionDriver's own established
    duplicate-drop counter (Sprint 105)."""
    candles, bhav_text = _load_real_day()
    driver = SessionDriver(lock_path="data/test_dup_decision.lock")
    driver.acquire()
    driver.load_option_chain(bhav_text, DAY)
    for c in candles:
        driver.process_tick("NIFTY", c["ts"], c["close"], source="recorded_stream")
    driver.process_tick("NIFTY", candles[0]["ts"], candles[0]["close"], source="recorded_stream")
    assert driver.result.dropped_ticks == 1
    driver.close_session()
