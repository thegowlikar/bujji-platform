"""Phase 19.20.4 -- intraday_checks tests. Synthetic fixtures only."""
from __future__ import annotations

from bujji.market_data_integrity.intraday_checks import (
    analyze_feed_interruptions,
    detect_duplicates,
    detect_missing_minutes,
    validate_timestamps,
)
from bujji.market_data_integrity.models import (
    CATEGORY_DUPLICATE_DATA,
    CATEGORY_FEED_INTERRUPTION,
    CATEGORY_MISSING_DATA,
    CATEGORY_TIMESTAMP_ERROR,
    SEVERITY_FAILED,
)
from bujji.market_microstructure.models import KIND_SPOT, MinuteObservation

DAY = "2026-08-17"
INSTRUMENT = "NSE:NIFTY50-INDEX"
SESSION_START = f"{DAY}T09:15:00+05:30"
SESSION_END = f"{DAY}T09:20:00+05:30"   # short synthetic "session" for tight fixtures
NOW = f"{DAY}T23:59:59+05:30"


def _obs(hms: str, **overrides):
    ws = f"{DAY}T{hms}+05:30"
    hh, mm, ss = hms.split(":")
    we_mm = int(mm) + 1
    we = f"{DAY}T{hh}:{we_mm:02d}:{ss}+05:30"
    fields = dict(
        instrument=INSTRUMENT, kind=KIND_SPOT, session_date=DAY,
        window_start=ws, window_end=we,
        open=24500.0, high=24502.0, low=24498.0, close=24501.0, tick_count=4,
        max_tick_silence_seconds=10.0, avg_tick_interval_seconds=10.0, max_price_move=2.0,
        first_tick_timestamp=ws, last_tick_timestamp=we,
    )
    fields.update(overrides)
    return MinuteObservation(**fields)


# --------------------------------------------------------------------- #
# Missing-minute detection
# --------------------------------------------------------------------- #
def test_clean_series_has_no_missing_minutes():
    observations = [_obs("09:15:00"), _obs("09:16:00"), _obs("09:17:00"), _obs("09:18:00"), _obs("09:19:00")]
    issues = detect_missing_minutes(
        observations, instrument=INSTRUMENT, session_start_iso=SESSION_START, session_end_iso=SESSION_END,
    )
    assert issues == []


def test_missing_single_minute_reports_exact_timestamp():
    # 09:15, 09:17, 09:18 present -- 09:16 missing (the spec's own worked
    # example). SESSION_END is 09:20, so 09:19 is ALSO a genuine, honest
    # gap between the last real observation and the declared session end
    # -- never fabricated, just also correctly reported.
    observations = [_obs("09:15:00"), _obs("09:17:00"), _obs("09:18:00")]
    issues = detect_missing_minutes(
        observations, instrument=INSTRUMENT, session_start_iso=SESSION_START, session_end_iso=SESSION_END,
    )
    missing_timestamps = [i.timestamp for i in issues if i.category == CATEGORY_MISSING_DATA]
    assert missing_timestamps == [f"{DAY}T09:16:00+05:30", f"{DAY}T09:19:00+05:30"]


def test_missing_minutes_at_session_boundaries_are_detected():
    # Nothing captured before 09:17 -- 09:15 and 09:16 are missing at the START boundary.
    observations = [_obs("09:17:00"), _obs("09:18:00"), _obs("09:19:00")]
    issues = detect_missing_minutes(
        observations, instrument=INSTRUMENT, session_start_iso=SESSION_START, session_end_iso=SESSION_END,
    )
    missing_timestamps = {i.timestamp for i in issues}
    assert missing_timestamps == {f"{DAY}T09:15:00+05:30", f"{DAY}T09:16:00+05:30"}


def test_zero_observations_reports_the_entire_session_as_missing():
    issues = detect_missing_minutes(
        [], instrument=INSTRUMENT, session_start_iso=SESSION_START, session_end_iso=SESSION_END,
    )
    assert len(issues) == 5   # 09:15..09:19 inclusive, 5 one-minute boundaries


# --------------------------------------------------------------------- #
# Duplicate detection
# --------------------------------------------------------------------- #
def test_identical_duplicate_is_detected_as_warning_not_silently_dropped():
    a = _obs("09:15:00")
    a_again = _obs("09:15:00")   # identical content, same instrument+window_start
    issues = detect_duplicates([a, a_again])
    assert len(issues) == 1
    assert issues[0].category == CATEGORY_DUPLICATE_DATA
    assert issues[0].severity != SEVERITY_FAILED   # redundant, not corrupting


def test_conflicting_duplicate_is_detected_as_failed():
    a = _obs("09:15:00", close=24501.0)
    b = _obs("09:15:00", close=24999.0)   # SAME window_start, DIFFERENT content
    issues = detect_duplicates([a, b])
    assert len(issues) == 1
    assert issues[0].severity == SEVERITY_FAILED
    assert issues[0].category == CATEGORY_DUPLICATE_DATA


def test_no_duplicates_in_a_clean_series():
    observations = [_obs("09:15:00"), _obs("09:16:00")]
    assert detect_duplicates(observations) == []


# --------------------------------------------------------------------- #
# Timestamp validation
# --------------------------------------------------------------------- #
def test_malformed_timestamp_is_flagged():
    bad = _obs("09:15:00", window_start="not-a-timestamp")
    issues = validate_timestamps([bad], session_start_iso=SESSION_START, session_end_iso=SESSION_END, now_iso=NOW)
    assert any(i.category == CATEGORY_TIMESTAMP_ERROR and i.severity == SEVERITY_FAILED for i in issues)


def test_future_timestamp_is_flagged():
    future = _obs("09:15:00", window_start=f"{DAY}T09:15:00+05:30", window_end="2099-01-01T00:00:00+05:30")
    issues = validate_timestamps(
        [future], session_start_iso=SESSION_START, session_end_iso=SESSION_END, now_iso=NOW,
    )
    assert any("future" in i.description for i in issues)


def test_reversed_window_is_flagged():
    reversed_obs = _obs("09:15:00", window_start=f"{DAY}T09:16:00+05:30", window_end=f"{DAY}T09:15:00+05:30")
    issues = validate_timestamps(
        [reversed_obs], session_start_iso=SESSION_START, session_end_iso=SESSION_END, now_iso=NOW,
    )
    assert any("window_end" in i.description for i in issues)


def test_observation_outside_session_window_is_flagged():
    outside = _obs("09:15:00", window_start=f"{DAY}T08:00:00+05:30", window_end=f"{DAY}T08:01:00+05:30")
    issues = validate_timestamps(
        [outside], session_start_iso=SESSION_START, session_end_iso=SESSION_END, now_iso=NOW,
    )
    assert any("session window" in i.description for i in issues)


def test_clean_timestamps_produce_no_issues():
    observations = [_obs("09:15:00"), _obs("09:16:00")]
    issues = validate_timestamps(
        observations, session_start_iso=SESSION_START, session_end_iso=SESSION_END, now_iso=NOW,
    )
    assert issues == []


# --------------------------------------------------------------------- #
# Feed interruption analysis
# --------------------------------------------------------------------- #
def test_no_session_log_rows_is_advisory_not_failed():
    issues = analyze_feed_interruptions([])
    assert len(issues) == 1
    assert issues[0].category == CATEGORY_FEED_INTERRUPTION


def test_disconnects_with_no_error_is_warning():
    rows = [{"session_date": DAY, "started_at": SESSION_START, "stopped_at": SESSION_END,
             "connect_count": 2, "disconnect_events": 1, "last_error": None}]
    issues = analyze_feed_interruptions(rows)
    assert len(issues) == 1
    assert issues[0].severity != SEVERITY_FAILED


def test_disconnects_with_error_is_failed():
    rows = [{"session_date": DAY, "started_at": SESSION_START, "stopped_at": SESSION_END,
             "connect_count": 1, "disconnect_events": 3, "last_error": "connection_reset"}]
    issues = analyze_feed_interruptions(rows)
    assert issues[0].severity == SEVERITY_FAILED


def test_clean_session_log_no_disconnects_produces_no_issue():
    rows = [{"session_date": DAY, "started_at": SESSION_START, "stopped_at": SESSION_END,
             "connect_count": 1, "disconnect_events": 0, "last_error": None}]
    assert analyze_feed_interruptions(rows) == []
