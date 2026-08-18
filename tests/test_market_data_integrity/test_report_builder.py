"""Phase 19.20.4 -- build_daily_integrity_report end-to-end tests."""
from __future__ import annotations

from bujji.historical_reality.capture import build_historical_observation
from bujji.market_data_integrity.eod_reconciliation import build_daily_integrity_report
from bujji.market_data_integrity.models import STATUS_FAILED, STATUS_GREEN, STATUS_WARNING
from bujji.market_observation.taxonomy import RESOLUTION_FIVE_MINUTE
from bujji.market_microstructure.models import KIND_SPOT, MinuteObservation

DAY = "2026-08-17"
INSTRUMENT = "NSE:NIFTY50-INDEX"
SESSION_START = f"{DAY}T09:15:00+05:30"
SESSION_END = f"{DAY}T09:20:00+05:30"
NOW = f"{DAY}T23:59:59+05:30"


def _minute_obs(hms: str, close: float):
    hh, mm, ss = hms.split(":")
    ws = f"{DAY}T{hms}+05:30"
    we = f"{DAY}T{hh}:{int(mm) + 1:02d}:{ss}+05:30"
    return MinuteObservation(
        instrument=INSTRUMENT, kind=KIND_SPOT, session_date=DAY,
        window_start=ws, window_end=we, open=close, high=close, low=close, close=close, tick_count=1,
        max_tick_silence_seconds=None, avg_tick_interval_seconds=None, max_price_move=0.0,
        first_tick_timestamp=ws, last_tick_timestamp=ws,
    )


def _hist_five_min(hms: str, *, open_, high, low, close):
    return build_historical_observation(
        instrument_identity=INSTRUMENT, instrument_type="INDEX", resolution=RESOLUTION_FIVE_MINUTE,
        timestamp=f"{DAY}T{hms}+05:30", payload={"open": open_, "high": high, "low": low, "close": close},
        source="fyers_historical", access_method="direct_sdk_fyers_historical_rest",
        source_epoch=0, source_symbol=INSTRUMENT, raw_artifact_ref="test", ingestion_run_id="test",
        retrieved_at=f"{DAY}T16:00:00+05:30", certification_status="test",
    )


def _clean_session_log():
    return [{"session_date": DAY, "started_at": SESSION_START, "stopped_at": SESSION_END,
              "connect_count": 1, "disconnect_events": 0, "last_error": None}]


def test_clean_day_produces_green_report():
    minutes = [_minute_obs(m, 24500.0 + i) for i, m in enumerate(
        ["09:15:00", "09:16:00", "09:17:00", "09:18:00", "09:19:00"]
    )]
    # Matches the 5-min fold exactly: open=first minute's open, close=last minute's close,
    # high=max of all, low=min of all -- minutes are flat bars at 24500..24504.
    historical = [_hist_five_min("09:15:00", open_=24500.0, high=24504.0, low=24500.0, close=24504.0)]

    report = build_daily_integrity_report(
        session_date=DAY, instrument=INSTRUMENT, minute_observations=minutes,
        session_start_iso=SESSION_START, session_end_iso=SESSION_END, now_iso=NOW,
        capture_session_log_rows=_clean_session_log(), five_minute_historical_rows=historical,
        bhavcopy_row={"ClsPric": "24504.00"},
    )
    assert report.overall_status == STATUS_GREEN
    assert report.capture_status == "COMPLETE"
    assert report.missing_intervals == ()
    assert report.quality_score == 100.0


def test_missing_data_day_is_not_green():
    # Only 09:15 and 09:18 present -- 09:16, 09:17, 09:19 missing.
    minutes = [_minute_obs("09:15:00", 24500.0), _minute_obs("09:18:00", 24501.0)]

    report = build_daily_integrity_report(
        session_date=DAY, instrument=INSTRUMENT, minute_observations=minutes,
        session_start_iso=SESSION_START, session_end_iso=SESSION_END, now_iso=NOW,
        capture_session_log_rows=_clean_session_log(), five_minute_historical_rows=(),
        bhavcopy_row=None,
    )
    assert report.overall_status in (STATUS_WARNING, STATUS_FAILED)
    assert report.capture_status == "PARTIAL"
    assert f"{DAY}T09:16:00+05:30" in report.missing_intervals
    assert f"{DAY}T09:17:00+05:30" in report.missing_intervals
    assert f"{DAY}T09:19:00+05:30" in report.missing_intervals
    assert report.quality_score < 100.0


def test_no_data_at_all_is_no_data_capture_status():
    report = build_daily_integrity_report(
        session_date=DAY, instrument=INSTRUMENT, minute_observations=[],
        session_start_iso=SESSION_START, session_end_iso=SESSION_END, now_iso=NOW,
        capture_session_log_rows=[], five_minute_historical_rows=(), bhavcopy_row=None,
    )
    assert report.capture_status == "NO_DATA"
    assert report.overall_status != STATUS_GREEN


def test_report_is_immutable_after_creation():
    import dataclasses

    import pytest

    report = build_daily_integrity_report(
        session_date=DAY, instrument=INSTRUMENT, minute_observations=[_minute_obs("09:15:00", 24500.0)],
        session_start_iso=SESSION_START, session_end_iso=SESSION_END, now_iso=NOW,
        capture_session_log_rows=_clean_session_log(), five_minute_historical_rows=(), bhavcopy_row=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.overall_status = STATUS_FAILED  # type: ignore[misc]
