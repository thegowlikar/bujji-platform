"""Phase 19.14.4 -- EOD Completeness Resolution Fix.

Phase 19.14.3's production re-verification found that
`validate_end_of_day_completeness()` always queried
`RESOLUTION_DAILY` data, but the only real writers
(`capture_market_reality_session.py`/`capture_options_reality_session.py`)
always write `RESOLUTION_FIVE_MINUTE` rows -- so every real,
successfully-captured trading day was reported EMPTY/incomplete. This
test proves the fix (additive `resolution`/`as_of_time` params,
defaulting to the exact prior behavior) against real, already-captured
production data on the VPS, and proves the old default behavior is
unchanged for any caller that doesn't opt in.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_reality_snapshot.models import RESOLUTION_DAILY
from bujji.shadow_runtime.completeness import validate_end_of_day_completeness

REAL_STORE_PATH = "data/historical_reality/normalized/historical_observations.db"
REAL_DATE_WITH_DATA = "2026-08-14"


def _store_available() -> bool:
    return os.path.exists(REAL_STORE_PATH)


@pytest.mark.skipif(not _store_available(), reason="real production HistoricalObservationStore not present in this environment")
def test_default_resolution_unchanged_still_reports_empty_for_five_minute_only_data():
    """The OLD default (RESOLUTION_DAILY, no as_of_time) must keep
    behaving exactly as before -- this is what proves the fix is
    additive, not a silent behavior change for any other caller."""
    from bujji.historical_reality.store import HistoricalObservationStore

    store = HistoricalObservationStore(REAL_STORE_PATH)
    now = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)
    report = validate_end_of_day_completeness(REAL_DATE_WITH_DATA, historical_store=store, now=now)
    assert report.completeness == "EMPTY"
    assert report.is_complete is False


# The next two tests load the REAL, ~1.7GB production database's full
# 2026-08-14 options chain (2,190 real contracts) -- genuinely,
# correctly memory-heavy, per this phase's own explicit "use real
# production data, not a fabricated test" requirement. Run in a
# subprocess rather than in-process: `resource.getrusage().ru_maxrss`
# (used by `bujji.live_shadow_operator.health.build_health_snapshot`,
# unrelated pre-existing modules) is a process-wide, monotonically-
# increasing high-water mark -- loading this much real data in-process
# would permanently push the *entire pytest run's* peak RSS past
# `MEMORY_WARNING_KB` (500MB), turning three unrelated, otherwise-passing
# health tests elsewhere in the suite (`test_sprint112_hardening.py`,
# `test_sprint4_ops.py`, `test_tick_silence_watchdog.py`) from GREEN to
# AMBER for the rest of the run -- confirmed by isolating the exact
# trigger during this fix's own verification. Subprocess isolation gets
# the same real proof without polluting a shared process-wide metric
# nothing in this test's own logic is responsible for.

def _run_real_data_probe(script: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=os.getcwd(), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"probe subprocess failed: {result.stderr[-2000:]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not _store_available(), reason="real production HistoricalObservationStore not present in this environment")
def test_five_minute_resolution_correctly_finds_real_captured_data():
    """The FIX: passing resolution=RESOLUTION_FIVE_MINUTE + a real
    as_of_time finds the real, already-captured data for a day
    independently proven (Phase 19.14.0/19.14.1/19.14.3) to have real
    spot/vix/options -- 2026-08-14."""
    script = f"""
import json
from datetime import datetime, timezone
from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_reality_snapshot.models import RESOLUTION_FIVE_MINUTE
from bujji.shadow_runtime.completeness import validate_end_of_day_completeness

store = HistoricalObservationStore({REAL_STORE_PATH!r})
now = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)
as_of_time = {REAL_DATE_WITH_DATA!r} + "T15:40:00+05:30"
report = validate_end_of_day_completeness(
    {REAL_DATE_WITH_DATA!r}, historical_store=store, now=now,
    resolution=RESOLUTION_FIVE_MINUTE, as_of_time=as_of_time,
)
print(json.dumps({{
    "spot_present": report.spot_present, "options_present": report.options_present,
    "vix_present": report.vix_present, "completeness": report.completeness, "is_complete": report.is_complete,
}}))
"""
    result = _run_real_data_probe(script)
    assert result["spot_present"] is True
    assert result["options_present"] is True
    assert result["vix_present"] is True
    assert result["completeness"] == "COMPLETE"
    assert result["is_complete"] is True


@pytest.mark.skipif(not _store_available(), reason="real production HistoricalObservationStore not present in this environment")
def test_production_completeness_fn_reports_complete_after_fix():
    """The exact, real, deployed function `run_daily_intelligence_session._make_real_completeness_fn`
    -- not a re-implementation -- now correctly reports COMPLETE for a
    real day with real, complete data."""
    script = f"""
import asyncio, json
import run_daily_intelligence_session as rdis

async def main():
    fn = rdis._make_real_completeness_fn({REAL_DATE_WITH_DATA!r})
    result = await fn()
    print(json.dumps({{
        "ran": result.ran, "is_complete": result.is_complete,
        "completeness_status": result.completeness_status, "missing_components": list(result.missing_components),
    }}))

asyncio.run(main())
"""
    result = _run_real_data_probe(script)
    assert result["ran"] is True
    assert result["is_complete"] is True
    assert result["completeness_status"] == "COMPLETE"
    assert result["missing_components"] == []


def test_validate_end_of_day_completeness_still_defaults_to_resolution_daily():
    """Signature-level guarantee: the default parameter values are
    unchanged, so any caller written before this fix keeps compiling
    and behaving identically without modification."""
    import inspect
    sig = inspect.signature(validate_end_of_day_completeness)
    assert sig.parameters["resolution"].default == RESOLUTION_DAILY
    assert sig.parameters["as_of_time"].default is None


# ---------------------------------------------------------------------
# EOD behavior matrix -- A-F, against synthetic FIVE_MINUTE fixtures
# using the exact same construction path (`build_historical_observation`)
# already established in tests/test_market_reality_snapshot_readiness.py
# -- never a second fixture convention.
# ---------------------------------------------------------------------
import tempfile
from pathlib import Path

from bujji.historical_reality.capture import build_historical_observation
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_reality_snapshot.builder import OPTIONS_UNDERLYING
from bujji.market_reality_snapshot.models import RESOLUTION_FIVE_MINUTE

FIXTURE_SPOT_SYMBOL = "NSE:NIFTY50-INDEX"
FIXTURE_VIX_SYMBOL = "NSE:INDIAVIX-INDEX"
FIXTURE_FUTURES_SYMBOL = "NIFTY_FUT_CONTINUOUS"
FIXTURE_DATE = "2026-08-14"
FIXTURE_AS_OF = f"{FIXTURE_DATE}T15:40:00+05:30"


def _fixture_obs(identity, instrument_type, timestamp, payload, value_kind=moc_taxonomy.VALUE_KIND_OHLC):
    return build_historical_observation(
        instrument_identity=identity, instrument_type=instrument_type,
        resolution=RESOLUTION_FIVE_MINUTE, timestamp=timestamp, payload=payload,
        source="fyers", access_method="test_access_method",
        source_epoch=1755000000, source_symbol=identity,
        raw_artifact_ref="", ingestion_run_id="RUN-19144-eod", retrieved_at=timestamp,
        certification_status="CERTIFIED_AVAILABLE", certification_ref="ref-1",
        value_kind=value_kind,
    )


@pytest.fixture
def fixture_store():
    with tempfile.TemporaryDirectory() as d:
        yield HistoricalObservationStore(str(Path(d) / "hist.db"))


def _write_spot(store, ts=FIXTURE_AS_OF):
    store.write(_fixture_obs(FIXTURE_SPOT_SYMBOL, "SPOT", ts, {"open": 24390, "high": 24400, "low": 24380, "close": 24395.55}))


def _write_vix(store, ts=FIXTURE_AS_OF):
    store.write(_fixture_obs(FIXTURE_VIX_SYMBOL, "INDEX", ts, {"open": 11.2, "high": 11.4, "low": 11.1, "close": 11.32}))


def _write_futures(store, ts=FIXTURE_AS_OF):
    store.write(_fixture_obs(FIXTURE_FUTURES_SYMBOL, "FUTURE", ts, {"open": 24400, "high": 24410, "low": 24390, "close": 24405}))


def _write_options(store, ts=FIXTURE_AS_OF):
    store.write(_fixture_obs(f"{OPTIONS_UNDERLYING}|2026-08-21|24450|CE", "OPTION", ts, {"ltp": 80.0}, value_kind=moc_taxonomy.VALUE_KIND_MAPPING))
    store.write(_fixture_obs(f"{OPTIONS_UNDERLYING}|2026-08-21|24450|PE", "OPTION", ts, {"ltp": 78.0}, value_kind=moc_taxonomy.VALUE_KIND_MAPPING))


def test_A_complete_trading_day_reports_complete(fixture_store):
    _write_spot(fixture_store)
    _write_futures(fixture_store)
    _write_vix(fixture_store)
    _write_options(fixture_store)
    now = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)
    report = validate_end_of_day_completeness(
        FIXTURE_DATE, historical_store=fixture_store, now=now,
        resolution=RESOLUTION_FIVE_MINUTE, as_of_time=FIXTURE_AS_OF,
    )
    assert report.completeness == "COMPLETE"
    assert report.is_complete is True
    assert report.spot_present and report.options_present and report.vix_present


def test_B_missing_vix_reports_incomplete(fixture_store):
    _write_spot(fixture_store)
    _write_options(fixture_store)
    # No VIX written.
    now = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)
    report = validate_end_of_day_completeness(
        FIXTURE_DATE, historical_store=fixture_store, now=now,
        resolution=RESOLUTION_FIVE_MINUTE, as_of_time=FIXTURE_AS_OF,
    )
    assert report.is_complete is False
    assert report.vix_present is False
    assert report.spot_present is True and report.options_present is True
    assert report.completeness == "PARTIAL"  # some but not all instruments present.


def test_C_missing_options_reports_incomplete(fixture_store):
    _write_spot(fixture_store)
    _write_vix(fixture_store)
    # No options written.
    now = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)
    report = validate_end_of_day_completeness(
        FIXTURE_DATE, historical_store=fixture_store, now=now,
        resolution=RESOLUTION_FIVE_MINUTE, as_of_time=FIXTURE_AS_OF,
    )
    assert report.is_complete is False
    assert report.options_present is False
    assert report.spot_present is True and report.vix_present is True
    # `completeness` (COMPLETE/PARTIAL/EMPTY) is derived from spot/futures/vix
    # only (per market_reality_snapshot.builder._classify_completeness,
    # unmodified) -- options are a SEPARATE presence check this module
    # layers on top, per its own pre-existing, unmodified design. The
    # daily runtime's own `is_complete` (spot AND options AND vix) is
    # correctly False here regardless of the underlying `completeness`
    # label.
    assert report.completeness in ("COMPLETE", "PARTIAL")


def test_D_missing_spot_reports_incomplete_and_empty(fixture_store):
    _write_vix(fixture_store)
    _write_options(fixture_store)
    # No spot written -- the one field every downstream brain needs most.
    now = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)
    report = validate_end_of_day_completeness(
        FIXTURE_DATE, historical_store=fixture_store, now=now,
        resolution=RESOLUTION_FIVE_MINUTE, as_of_time=FIXTURE_AS_OF,
    )
    assert report.is_complete is False
    assert report.spot_present is False


def test_E_empty_observation_set_reports_empty(fixture_store):
    # Nothing written at all.
    now = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)
    report = validate_end_of_day_completeness(
        FIXTURE_DATE, historical_store=fixture_store, now=now,
        resolution=RESOLUTION_FIVE_MINUTE, as_of_time=FIXTURE_AS_OF,
    )
    assert report.is_complete is False
    assert report.spot_present is False and report.options_present is False and report.vix_present is False
    assert report.completeness == "EMPTY"


def test_F_weekend_non_trading_day_reports_empty_not_a_fabricated_state(fixture_store):
    """No new classification invented: a weekend/non-trading day
    produces zero real observations (nothing was ever captured, since
    `within_market_hours()` in the capture scripts refuses to run on a
    weekend before any broker call -- Phase 19.14.0's own confirmed
    finding), so it collapses to the SAME EMPTY completeness state as
    §E -- the existing model already represents "nothing observed" this
    way; a distinct NOT-A-TRADING-DAY enum would duplicate that meaning
    without adding real information the caller can't already get from
    `completeness == EMPTY`."""
    weekend_date = "2026-08-15"  # a real Saturday (2026-08-15 IST).
    import datetime as _dt
    assert _dt.date.fromisoformat(weekend_date).weekday() >= 5
    now = datetime(2026, 8, 16, 10, 0, 0, tzinfo=timezone.utc)
    report = validate_end_of_day_completeness(
        weekend_date, historical_store=fixture_store, now=now,
        resolution=RESOLUTION_FIVE_MINUTE, as_of_time=f"{weekend_date}T15:40:00+05:30",
    )
    assert report.completeness == "EMPTY"
    assert report.is_complete is False
