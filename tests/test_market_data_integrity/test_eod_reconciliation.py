"""Phase 19.20.4 -- eod_reconciliation tests. Synthetic fixtures only."""
from __future__ import annotations

from bujji.historical_reality.capture import build_historical_observation
from bujji.market_data_integrity.eod_reconciliation import (
    aggregate_one_minute_to_five_minute,
    compare_against_bhavcopy,
    compare_five_minute_series,
    find_bhavcopy_row,
)
from bujji.market_data_integrity.models import (
    CATEGORY_BHAVCOPY_MISMATCH,
    CATEGORY_OHLC_MISMATCH,
    SEVERITY_ADVISORY,
    SEVERITY_FAILED,
)
from bujji.market_observation.taxonomy import RESOLUTION_FIVE_MINUTE
from bujji.market_microstructure.models import KIND_SPOT, MinuteObservation

DAY = "2026-08-17"
INSTRUMENT = "NSE:NIFTY50-INDEX"


def _minute_obs(hms: str, close: float, **overrides):
    ws = f"{DAY}T{hms}+05:30"
    fields = dict(
        instrument=INSTRUMENT, kind=KIND_SPOT, session_date=DAY,
        window_start=ws, window_end=ws, open=close, high=close, low=close, close=close, tick_count=1,
        max_tick_silence_seconds=None, avg_tick_interval_seconds=None, max_price_move=0.0,
        first_tick_timestamp=ws, last_tick_timestamp=ws,
    )
    fields.update(overrides)
    return MinuteObservation(**fields)


def _historical_five_min(hms: str, *, open_, high, low, close):
    return build_historical_observation(
        instrument_identity=INSTRUMENT, instrument_type="INDEX", resolution=RESOLUTION_FIVE_MINUTE,
        timestamp=f"{DAY}T{hms}+05:30", payload={"open": open_, "high": high, "low": low, "close": close},
        source="fyers_historical", access_method="direct_sdk_fyers_historical_rest",
        source_epoch=0, source_symbol=INSTRUMENT, raw_artifact_ref="test", ingestion_run_id="test",
        retrieved_at=f"{DAY}T16:00:00+05:30", certification_status="test",
    )


# --------------------------------------------------------------------- #
# 1-minute -> 5-minute aggregation
# --------------------------------------------------------------------- #
def test_aggregate_one_minute_folds_correctly_into_five_minute_buckets():
    minutes = [
        _minute_obs("09:15:00", 24500.0, open=24500.0, high=24503.0, low=24499.0),
        _minute_obs("09:16:00", 24502.0, open=24502.0, high=24504.0, low=24501.0),
        _minute_obs("09:17:00", 24505.0, open=24505.0, high=24506.0, low=24504.0),
        _minute_obs("09:18:00", 24501.0, open=24501.0, high=24502.0, low=24498.0),
        _minute_obs("09:19:00", 24503.0, open=24503.0, high=24505.0, low=24500.0),
    ]
    buckets = aggregate_one_minute_to_five_minute(minutes)
    assert len(buckets) == 1
    bucket = buckets[f"{DAY}T09:15:00+05:30"]
    assert bucket["open"] == 24500.0        # earliest minute's open
    assert bucket["close"] == 24503.0       # latest minute's close
    assert bucket["high"] == 24506.0        # max of all highs
    assert bucket["low"] == 24498.0         # min of all lows
    assert bucket["member_count"] == 5


# --------------------------------------------------------------------- #
# 5-minute reconciliation
# --------------------------------------------------------------------- #
def test_five_minute_reconciliation_matches_within_tolerance():
    minutes = [_minute_obs("09:15:00", 24500.0, open=24500.0, high=24500.0, low=24500.0)]
    aggregated = aggregate_one_minute_to_five_minute(minutes)
    historical = [_historical_five_min("09:15:00", open_=24500.0, high=24500.0, low=24500.0, close=24500.0)]
    issues = compare_five_minute_series(aggregated, historical, instrument=INSTRUMENT)
    assert issues == []


def test_five_minute_reconciliation_deliberate_mismatch_is_failed():
    minutes = [_minute_obs("09:15:00", 24500.0, open=24500.0, high=24500.0, low=24500.0)]
    aggregated = aggregate_one_minute_to_five_minute(minutes)
    # Deliberately WRONG 5-minute data (Phase 19.19 side disagrees materially).
    historical = [_historical_five_min("09:15:00", open_=24500.0, high=24500.0, low=24500.0, close=25000.0)]
    issues = compare_five_minute_series(aggregated, historical, instrument=INSTRUMENT)
    assert len(issues) == 1
    assert issues[0].severity == SEVERITY_FAILED
    assert issues[0].category == CATEGORY_OHLC_MISMATCH


def test_five_minute_bucket_present_only_on_one_side_is_reported():
    minutes = [_minute_obs("09:15:00", 24500.0, open=24500.0, high=24500.0, low=24500.0)]
    aggregated = aggregate_one_minute_to_five_minute(minutes)
    issues = compare_five_minute_series(aggregated, [], instrument=INSTRUMENT)  # no historical data at all
    assert len(issues) == 1
    assert issues[0].severity == SEVERITY_FAILED


# --------------------------------------------------------------------- #
# Bhavcopy reconciliation
# --------------------------------------------------------------------- #
def _bhav_rows():
    return [
        {"TckrSymb": "NIFTY", "OptnTp": "", "StrkPric": "", "XpryDt": "2026-08-27", "ClsPric": "24501.00"},
        {"TckrSymb": "BANKNIFTY", "OptnTp": "", "StrkPric": "", "XpryDt": "2026-08-27", "ClsPric": "51000.00"},
    ]


def test_bhavcopy_matching_close_is_clean_no_issue():
    row = find_bhavcopy_row(_bhav_rows(), tckr_symb="NIFTY", xpry_dt="2026-08-27")
    assert row is not None
    issue = compare_against_bhavcopy(24501.00, row, instrument=INSTRUMENT)
    assert issue is None


def test_bhavcopy_small_rounding_difference_is_advisory():
    row = find_bhavcopy_row(_bhav_rows(), tckr_symb="NIFTY", xpry_dt="2026-08-27")
    issue = compare_against_bhavcopy(24501.30, row, instrument=INSTRUMENT)  # 0.30 diff
    assert issue is not None
    assert issue.severity == SEVERITY_ADVISORY
    assert issue.category == CATEGORY_BHAVCOPY_MISMATCH


def test_bhavcopy_material_mismatch_is_failed():
    row = find_bhavcopy_row(_bhav_rows(), tckr_symb="NIFTY", xpry_dt="2026-08-27")
    issue = compare_against_bhavcopy(24600.00, row, instrument=INSTRUMENT)  # 99-point diff
    assert issue is not None
    assert issue.severity == SEVERITY_FAILED


def test_missing_bhavcopy_row_is_advisory():
    issue = compare_against_bhavcopy(24501.00, None, instrument=INSTRUMENT)
    assert issue is not None
    assert issue.severity == SEVERITY_ADVISORY
    assert issue.category == CATEGORY_BHAVCOPY_MISMATCH


def test_find_bhavcopy_row_never_guesses_on_ambiguous_match():
    rows = _bhav_rows() + [{"TckrSymb": "NIFTY", "OptnTp": "", "StrkPric": "", "XpryDt": "2026-09-24",
                              "ClsPric": "24600.00"}]
    # Two NIFTY rows now exist (different expiries) -- without xpry_dt, this must be None, never a guess.
    row = find_bhavcopy_row(rows, tckr_symb="NIFTY")
    assert row is None
