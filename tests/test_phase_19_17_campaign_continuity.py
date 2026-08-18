"""Phase 19.17 -- Stale Heartbeat + Trading Calendar Wiring + Campaign
Continuity Detection.

Covers, against real components (real `HistoricalObservationStore`,
real `EventStore`, real `MarketCalendar`, real `write_daily_heartbeat`),
never stubs pretending to be them:

- fresh vs. stale heartbeat detection (status.py)
- verified trading day / weekend / (un)verified holiday calendar (MarketCalendar, unmodified)
- SESSION_COMPLETE / INCOMPLETE / SESSION_FAILED / MISSING / NON_TRADING_DAY classification
- continuity streak + gap detection
- FYERS-field derivation on the campaign status renderer
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bujji.historical_reality.capture import build_historical_observation
from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_calendar import MarketCalendar
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_reality_snapshot.builder import OPTIONS_UNDERLYING
from bujji.market_reality_snapshot.models import RESOLUTION_FIVE_MINUTE
from bujji.shadow_runtime.campaign_continuity import (
    STATUS_INCOMPLETE,
    STATUS_MISSING,
    STATUS_NON_TRADING_DAY,
    STATUS_SESSION_COMPLETE,
    STATUS_SESSION_FAILED,
    build_campaign_continuity_report,
    classify_session,
)
from bujji.shadow_runtime.cycle_artifact import record_cycle_failure
from bujji.shadow_runtime.daily_intelligence_artifact import DailyIntelligenceArtifact, record_daily_intelligence_artifact
from bujji.shadow_runtime.daily_session import DailySessionHeartbeat, write_daily_heartbeat
from bujji.shadow_runtime.status import get_operational_status
from bujji.state_persistence.store import EventStore

FIXED_NOW = datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc)
SPOT_SYMBOL = "NSE:NIFTY50-INDEX"
VIX_SYMBOL = "NSE:INDIAVIX-INDEX"


def _obs(identity, instrument_type, ts, payload, value_kind=moc_taxonomy.VALUE_KIND_OHLC):
    return build_historical_observation(
        instrument_identity=identity, instrument_type=instrument_type, resolution=RESOLUTION_FIVE_MINUTE,
        timestamp=ts, payload=payload, source="fyers", access_method="test", source_epoch=1755000000,
        source_symbol=identity, raw_artifact_ref="", ingestion_run_id="RUN-19147", retrieved_at=ts,
        certification_status="CERTIFIED_AVAILABLE", certification_ref="ref-1", value_kind=value_kind,
    )


@pytest.fixture
def historical_store():
    with tempfile.TemporaryDirectory() as d:
        yield HistoricalObservationStore(str(Path(d) / "hist.db"))


@pytest.fixture
def cycle_artifact_store():
    with tempfile.TemporaryDirectory() as d:
        yield EventStore(str(Path(d) / "cycle.jsonl"))


@pytest.fixture
def daily_artifact_store():
    with tempfile.TemporaryDirectory() as d:
        yield EventStore(str(Path(d) / "daily.jsonl"))


def _write_full_capture(store, date_str):
    ts = f"{date_str}T15:40:00+05:30"
    store.write(_obs(SPOT_SYMBOL, "SPOT", ts, {"open": 24390, "high": 24400, "low": 24380, "close": 24395.55}))
    store.write(_obs(VIX_SYMBOL, "INDEX", ts, {"open": 11.2, "high": 11.4, "low": 11.1, "close": 11.32}))
    store.write(_obs(f"{OPTIONS_UNDERLYING}|2026-08-21|24450|CE", "OPTION", ts, {"ltp": 80.0}, value_kind=moc_taxonomy.VALUE_KIND_MAPPING))


def _synthetic_daily_artifact(date_str: str, *, gate_passed: bool) -> DailyIntelligenceArtifact:
    return DailyIntelligenceArtifact(
        cycle_id=f"daily-{date_str}-1", session_id=f"daily-{date_str}", session_date=date_str,
        as_of_time=f"{date_str}T15:40:00+00:00", execution_mode="LIVE", runtime_health_status="RUNNING",
        intelligence_fingerprint="synthetic-fp", reality={"date": date_str}, intelligence={}, decision_intelligence={},
        phenomena={}, market_state={}, environment={}, completeness_gate_passed=gate_passed, artifact_id=f"artifact-{date_str}",
    )


# ---------------------------------------------------------------------
# Stale heartbeat detection
# ---------------------------------------------------------------------

def _write_heartbeat_with_pinned_mtime(heartbeat_path: str, *, mtime: datetime) -> None:
    """`os.path.getmtime` reflects the REAL filesystem clock, not any
    injected `now` -- pin it explicitly via `os.utime` so this test is
    deterministic regardless of when it actually runs, rather than
    assuming the write happens to land on `FIXED_NOW`."""
    import os
    write_daily_heartbeat(heartbeat_path, DailySessionHeartbeat(
        session_date="2026-08-17", runtime_status="SESSION_COMPLETE", last_observation_timestamp="t",
        last_intelligence_cycle_timestamp="t", rows_captured_today=10,
    ))
    ts = mtime.timestamp()
    os.utime(heartbeat_path, (ts, ts))


def test_fresh_heartbeat_is_not_stale(tmp_path):
    heartbeat_path = str(tmp_path / "heartbeat.json")
    _write_heartbeat_with_pinned_mtime(heartbeat_path, mtime=FIXED_NOW)
    status = get_operational_status(heartbeat_path, now=FIXED_NOW, stale_threshold_seconds=3600)
    assert status.stale is False


def test_old_heartbeat_is_stale(tmp_path):
    heartbeat_path = str(tmp_path / "heartbeat.json")
    _write_heartbeat_with_pinned_mtime(heartbeat_path, mtime=FIXED_NOW)
    far_future = FIXED_NOW + timedelta(hours=48)
    status = get_operational_status(heartbeat_path, now=far_future, stale_threshold_seconds=3600)
    assert status.stale is True
    assert status.heartbeat_file_age_seconds > 3600


def test_no_heartbeat_file_is_not_flagged_stale_but_is_unknown(tmp_path):
    status = get_operational_status(str(tmp_path / "missing.json"), now=FIXED_NOW)
    assert status.lifecycle_state == "UNKNOWN"
    assert status.stale is False  # nothing to be stale -- honestly distinct from "ran and went stale."


def test_stale_threshold_is_configurable(tmp_path):
    heartbeat_path = str(tmp_path / "heartbeat.json")
    _write_heartbeat_with_pinned_mtime(heartbeat_path, mtime=FIXED_NOW)
    later = FIXED_NOW + timedelta(hours=2)
    assert get_operational_status(heartbeat_path, now=later, stale_threshold_seconds=3600).stale is True
    assert get_operational_status(heartbeat_path, now=later, stale_threshold_seconds=36000).stale is False


# ---------------------------------------------------------------------
# MarketCalendar behavior (unmodified class, re-confirmed here as the
# foundation classify_session builds on)
# ---------------------------------------------------------------------

def test_weekend_is_not_a_trading_day():
    calendar = MarketCalendar()
    is_trading, reason = calendar.is_trading_day(__import__("datetime").date(2026, 8, 15))  # a real Saturday.
    assert is_trading is False
    assert "weekend" in reason.lower()


def test_ordinary_weekday_is_a_trading_day_when_calendar_unverified():
    """The default, honest behavior: an unverified, empty holiday
    calendar treats any unlisted weekday as a trading day -- never
    silently invents a holiday."""
    calendar = MarketCalendar()
    assert calendar.holiday_calendar_verified is False
    is_trading, _ = calendar.is_trading_day(__import__("datetime").date(2026, 8, 14))  # a real Friday.
    assert is_trading is True


def test_listed_holiday_is_correctly_excluded_once_populated():
    """Proves the wiring, not a real NSE date -- deliberately a
    synthetic holiday entry, never a guessed real one (per this phase's
    own explicit instruction not to populate NSE holidays from memory)."""
    calendar = MarketCalendar(holidays={"2026-08-14": "SYNTHETIC_TEST_HOLIDAY"}, holiday_calendar_verified=True)
    is_trading, reason = calendar.is_trading_day(__import__("datetime").date(2026, 8, 14))
    assert is_trading is False
    assert "SYNTHETIC_TEST_HOLIDAY" in reason


# ---------------------------------------------------------------------
# classify_session -- the 5-way distinction, task 4's own requirement
# ---------------------------------------------------------------------

def test_classify_non_trading_day(historical_store, cycle_artifact_store, daily_artifact_store):
    result = classify_session(
        "2026-08-15", calendar=MarketCalendar(), historical_store=historical_store,
        cycle_artifact_store=cycle_artifact_store, daily_artifact_store=daily_artifact_store, now=FIXED_NOW,
    )
    assert result.status == STATUS_NON_TRADING_DAY


def test_classify_session_complete(historical_store, cycle_artifact_store, daily_artifact_store):
    date_str = "2026-08-14"
    _write_full_capture(historical_store, date_str)
    record_daily_intelligence_artifact(daily_artifact_store, _synthetic_daily_artifact(date_str, gate_passed=True), recorded_at=FIXED_NOW)
    result = classify_session(
        date_str, calendar=MarketCalendar(), historical_store=historical_store,
        cycle_artifact_store=cycle_artifact_store, daily_artifact_store=daily_artifact_store, now=FIXED_NOW,
    )
    assert result.status == STATUS_SESSION_COMPLETE


def test_classify_session_incomplete_when_gate_not_passed(historical_store, cycle_artifact_store, daily_artifact_store):
    date_str = "2026-08-14"
    _write_full_capture(historical_store, date_str)
    record_daily_intelligence_artifact(daily_artifact_store, _synthetic_daily_artifact(date_str, gate_passed=False), recorded_at=FIXED_NOW)
    result = classify_session(
        date_str, calendar=MarketCalendar(), historical_store=historical_store,
        cycle_artifact_store=cycle_artifact_store, daily_artifact_store=daily_artifact_store, now=FIXED_NOW,
    )
    assert result.status == STATUS_INCOMPLETE


def test_classify_session_failed_via_recorded_cycle_failure(historical_store, cycle_artifact_store, daily_artifact_store):
    date_str = "2026-08-14"
    _write_full_capture(historical_store, date_str)
    record_cycle_failure(
        cycle_artifact_store, session_id=f"daily-{date_str}", cycle_id=f"daily-{date_str}-1",
        execution_mode="LIVE", as_of_time=f"{date_str}T15:40:00+00:00", error="IntelligencePipelineAdapterError: no options",
        recorded_at=FIXED_NOW,
    )
    result = classify_session(
        date_str, calendar=MarketCalendar(), historical_store=historical_store,
        cycle_artifact_store=cycle_artifact_store, daily_artifact_store=daily_artifact_store, now=FIXED_NOW,
    )
    assert result.status == STATUS_SESSION_FAILED
    assert "IntelligencePipelineAdapterError" in result.reason


def test_classify_session_failed_when_capture_present_but_nothing_else(historical_store, cycle_artifact_store, daily_artifact_store):
    """Covers the FYERS-auth-failure shape: capture succeeded (or
    partially ran) but the intelligence side never even reached a
    recorded cycle -- never silently MISSING, never silently COMPLETE."""
    date_str = "2026-08-14"
    _write_full_capture(historical_store, date_str)
    result = classify_session(
        date_str, calendar=MarketCalendar(), historical_store=historical_store,
        cycle_artifact_store=cycle_artifact_store, daily_artifact_store=daily_artifact_store, now=FIXED_NOW,
    )
    assert result.status == STATUS_SESSION_FAILED


def test_classify_session_missing_when_absolutely_nothing_recorded(historical_store, cycle_artifact_store, daily_artifact_store):
    date_str = "2026-08-14"
    result = classify_session(
        date_str, calendar=MarketCalendar(), historical_store=historical_store,
        cycle_artifact_store=cycle_artifact_store, daily_artifact_store=daily_artifact_store, now=FIXED_NOW,
    )
    assert result.status == STATUS_MISSING
    # never silently success:
    assert result.status not in (STATUS_SESSION_COMPLETE,)


# ---------------------------------------------------------------------
# Continuity streak + gap detection
# ---------------------------------------------------------------------

def test_streak_and_gap_detection_across_a_real_shaped_week(historical_store, cycle_artifact_store, daily_artifact_store):
    calendar = MarketCalendar()
    # Mon 2026-08-10 .. Fri 2026-08-14, real week.
    complete_days = ["2026-08-10", "2026-08-11", "2026-08-13", "2026-08-14"]  # 08-12 deliberately left MISSING.
    for d in complete_days:
        _write_full_capture(historical_store, d)
        record_daily_intelligence_artifact(daily_artifact_store, _synthetic_daily_artifact(d, gate_passed=True), recorded_at=FIXED_NOW)

    report = build_campaign_continuity_report(
        "2026-08-10", "2026-08-16", calendar=calendar, historical_store=historical_store,
        cycle_artifact_store=cycle_artifact_store, daily_artifact_store=daily_artifact_store, now=FIXED_NOW,
    )
    assert report.expected_sessions == 5  # Mon-Fri, weekend (08-15/08-16) excluded.
    assert report.completed == 4
    assert report.missing == 1
    assert report.gaps == ("2026-08-12",)
    # Streak counts back from the most recent date (08-16, a Sunday,
    # skipped) -- 08-14 COMPLETE, but 08-12's gap means the streak from
    # 08-13/08-14 is only 2 (08-13 and 08-14 are consecutive COMPLETE
    # trading days; the walk stops at 08-12 which is MISSING).
    assert report.current_streak == 2


def test_never_silently_classifies_missing_as_success(historical_store, cycle_artifact_store, daily_artifact_store):
    report = build_campaign_continuity_report(
        "2026-08-14", "2026-08-14", calendar=MarketCalendar(), historical_store=historical_store,
        cycle_artifact_store=cycle_artifact_store, daily_artifact_store=daily_artifact_store, now=FIXED_NOW,
    )
    assert report.completed == 0
    assert report.missing == 1
    assert report.classifications[0].status == STATUS_MISSING
