"""Phase 18.7 -- Research Dataset Governance Layer tests:
ResearchCalendar + DatasetVersion, built entirely on real,
already-proven primitives (fingerprint(), reconstruction_version,
readiness, MarketCalendar)."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import pytest

from bujji.historical_reality.capture import build_historical_observation
from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_calendar import MarketCalendar
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_reality_snapshot.builder import OPTIONS_UNDERLYING
from bujji.market_reality_snapshot.dataset_version import build_dataset_version
from bujji.market_reality_snapshot.models import RESOLUTION_FIVE_MINUTE
from bujji.market_reality_snapshot.research_calendar import (
    STATUS_FAILED,
    STATUS_NON_TRADING_DAY,
    STATUS_PARTIAL,
    STATUS_READY,
    build_research_calendar,
    build_research_calendar_entry,
)

SPOT_SYMBOL = "NSE:NIFTY50-INDEX"
VIX_SYMBOL = "NSE:INDIAVIX-INDEX"
FUTURES_SYMBOL = "NIFTY_FUT_CONTINUOUS"


def _obs(identity, instrument_type, resolution, timestamp, payload, value_kind=moc_taxonomy.VALUE_KIND_OHLC,
         run_id="RUN-x"):
    return build_historical_observation(
        instrument_identity=identity, instrument_type=instrument_type,
        resolution=resolution, timestamp=timestamp, payload=payload,
        source="fyers", access_method="test_access_method",
        source_epoch=1755000000, source_symbol=identity,
        raw_artifact_ref="", ingestion_run_id=run_id, retrieved_at=timestamp,
        certification_status="CERTIFIED_AVAILABLE", certification_ref="ref-1",
        value_kind=value_kind,
    )


def _write_full_session(store, date_str, ts):
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 1, "high": 2, "low": 0.5, "close": 1.5}, run_id="RUN-spot"))
    store.write(_obs(FUTURES_SYMBOL, "FUTURE", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 1, "high": 2, "low": 0.5, "close": 1.5}, run_id="RUN-fut"))
    store.write(_obs(VIX_SYMBOL, "INDEX", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 12, "high": 12.5, "low": 11.5, "close": 12.1}, run_id="RUN-vix"))
    store.write(_obs(f"{OPTIONS_UNDERLYING}|2026-08-18|21800|CE", "OPTION", RESOLUTION_FIVE_MINUTE, ts,
                      {"ltp": 250.0}, value_kind=moc_taxonomy.VALUE_KIND_MAPPING, run_id="RUN-opt"))


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as d:
        yield HistoricalObservationStore(str(Path(d) / "hist.db"))


# --- Test 1: complete session -------------------------------------------------
def test_complete_session_is_ready(store):
    ts = "2026-08-14T09:20:00+05:30"
    _write_full_session(store, "2026-08-14", ts)
    entry = build_research_calendar_entry("2026-08-14", historical_store=store,
                                           resolution=RESOLUTION_FIVE_MINUTE, as_of_time=ts)
    assert entry.status == STATUS_READY
    assert entry.is_trading_day is True
    assert entry.readiness.is_complete


# --- Test 2: missing options ---------------------------------------------------
def test_missing_options_is_partial(store):
    ts = "2026-08-14T09:20:00+05:30"
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 1, "high": 2, "low": 0.5, "close": 1.5}))
    store.write(_obs(FUTURES_SYMBOL, "FUTURE", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 1, "high": 2, "low": 0.5, "close": 1.5}))
    store.write(_obs(VIX_SYMBOL, "INDEX", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 12, "high": 12.5, "low": 11.5, "close": 12.1}))
    entry = build_research_calendar_entry("2026-08-14", historical_store=store,
                                           resolution=RESOLUTION_FIVE_MINUTE, as_of_time=ts)
    assert entry.status == STATUS_PARTIAL
    assert entry.readiness.missing == ("options",)


# --- Test 3: missing futures ----------------------------------------------------
def test_missing_futures_is_partial(store):
    ts = "2026-08-14T09:20:00+05:30"
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 1, "high": 2, "low": 0.5, "close": 1.5}))
    store.write(_obs(VIX_SYMBOL, "INDEX", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 12, "high": 12.5, "low": 11.5, "close": 12.1}))
    store.write(_obs(f"{OPTIONS_UNDERLYING}|2026-08-18|21800|CE", "OPTION", RESOLUTION_FIVE_MINUTE, ts,
                      {"ltp": 250.0}, value_kind=moc_taxonomy.VALUE_KIND_MAPPING))
    entry = build_research_calendar_entry("2026-08-14", historical_store=store,
                                           resolution=RESOLUTION_FIVE_MINUTE, as_of_time=ts)
    assert entry.status == STATUS_PARTIAL
    assert entry.readiness.missing == ("futures",)


# --- Test 4: holiday (real weekend, zero data) -----------------------------------
def test_weekend_with_zero_data_is_non_trading_day_not_failed(store):
    """The exact real case Phase 18.4/18.5 hit by hand (2026-08-01) --
    a real Saturday with zero rows must never report FAILED (which
    would falsely imply a capture gap)."""
    entry = build_research_calendar_entry("2026-08-15", historical_store=store,
                                           resolution=RESOLUTION_FIVE_MINUTE, as_of_time="2026-08-15T09:20:00+05:30")
    assert entry.is_trading_day is False
    assert "weekend" in entry.trading_day_reason.lower()
    assert entry.status == STATUS_NON_TRADING_DAY
    assert entry.status != STATUS_FAILED


def test_unlisted_holiday_on_a_weekday_honestly_reports_failed_not_non_trading():
    """MarketCalendar's own disclosed limitation (Sprint 112,
    holiday_calendar_verified=False): an unlisted real NSE holiday on a
    weekday cannot be distinguished from a real capture gap -- this
    must report FAILED, not silently invent a NON_TRADING_DAY status
    the calendar has no evidence for."""
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        # 2026-08-18 is a real Tuesday (confirmed: date(2026,8,18).strftime('%A')
        # == "Tuesday") with an UNPOPULATED holiday calendar (the honest
        # default) and zero data written -- structurally identical to an
        # unlisted real NSE holiday landing on a weekday.
        entry = build_research_calendar_entry("2026-08-18",
                                               historical_store=store,
                                               resolution=RESOLUTION_FIVE_MINUTE,
                                               as_of_time="2026-08-18T09:20:00+05:30")
        assert entry.is_trading_day is True  # unverified holiday list -> honestly "don't know", defaults True
        assert entry.status == STATUS_FAILED


# --- Test 5: incomplete capture (mid-session gap within a day) -------------------
def test_incomplete_intraday_capture_is_partial_not_ready(store):
    """Simulates a real mid-session failure: spot/futures/vix landed,
    options capture died partway through the day (never wrote
    anything) -- must never round up to READY."""
    ts = "2026-08-14T09:20:00+05:30"
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 1, "high": 2, "low": 0.5, "close": 1.5}))
    store.write(_obs(FUTURES_SYMBOL, "FUTURE", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 1, "high": 2, "low": 0.5, "close": 1.5}))
    store.write(_obs(VIX_SYMBOL, "INDEX", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 12, "high": 12.5, "low": 11.5, "close": 12.1}))
    entry = build_research_calendar_entry("2026-08-14", historical_store=store,
                                           resolution=RESOLUTION_FIVE_MINUTE, as_of_time=ts)
    assert entry.status == STATUS_PARTIAL
    assert not entry.readiness.is_complete


# --- ResearchCalendar range behavior ---------------------------------------------
def test_calendar_range_never_skips_a_date_and_uses_per_date_as_of_time(store):
    ts14 = "2026-08-14T09:20:00+05:30"
    _write_full_session(store, "2026-08-14", ts14)
    entries = build_research_calendar("2026-08-13", "2026-08-15", historical_store=store,
                                       resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:20:00+05:30")
    dates = [e.date for e in entries]
    assert dates == ["2026-08-13", "2026-08-14", "2026-08-15"]
    statuses = {e.date: e.status for e in entries}
    assert statuses["2026-08-13"] == STATUS_FAILED  # trading day, zero data written in this isolated store.
    assert statuses["2026-08-14"] == STATUS_READY
    assert statuses["2026-08-15"] == STATUS_NON_TRADING_DAY


def test_calendar_range_rejects_end_before_start(store):
    with pytest.raises(ValueError):
        build_research_calendar("2026-08-15", "2026-08-14", historical_store=store)


# --- DatasetVersion ---------------------------------------------------------------
def test_dataset_version_assembles_real_fingerprints_and_run_ids(store):
    ts = "2026-08-14T09:20:00+05:30"
    _write_full_session(store, "2026-08-14", ts)
    dv = build_dataset_version("2026-08-14", "2026-08-14", historical_store=store,
                                resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:20:00+05:30")
    assert dv.ready_dates == ("2026-08-14",)
    assert dv.incomplete_dates == ()
    assert set(dv.included_components) == {"spot", "futures", "vix", "options"}
    assert set(dv.ingestion_run_references) == {"RUN-spot", "RUN-fut", "RUN-vix", "RUN-opt"}
    assert len(dv.fingerprint_lineage) == 1
    assert len(dv.dataset_version_id) == 64
    assert dv.reconstruction_version == "18.3.0"


def test_dataset_version_never_drops_incomplete_dates_silently(store):
    ts = "2026-08-14T09:20:00+05:30"
    _write_full_session(store, "2026-08-14", ts)
    dv = build_dataset_version("2026-08-13", "2026-08-15", historical_store=store,
                                resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:20:00+05:30")
    assert dv.ready_dates == ("2026-08-14",)
    assert dv.incomplete_dates == ("2026-08-13", "2026-08-15")
    assert len(dv.fingerprint_lineage) == 3  # every date, not just READY ones.


def test_dataset_version_id_reproducible(store):
    ts = "2026-08-14T09:20:00+05:30"
    _write_full_session(store, "2026-08-14", ts)
    dv1 = build_dataset_version("2026-08-14", "2026-08-14", historical_store=store,
                                 resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:20:00+05:30")
    dv2 = build_dataset_version("2026-08-14", "2026-08-14", historical_store=store,
                                 resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:20:00+05:30")
    assert dv1.dataset_version_id == dv2.dataset_version_id


def test_dataset_version_rejects_mixed_reconstruction_versions(store):
    """A dataset spanning a reconstruction-logic change must refuse to
    assemble silently -- proven by forcing two calendar entries to
    report different reconstruction_version values."""
    ts = "2026-08-14T09:20:00+05:30"
    _write_full_session(store, "2026-08-14", ts)
    entries = build_research_calendar("2026-08-14", "2026-08-14", historical_store=store,
                                       resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:20:00+05:30")
    import dataclasses
    mismatched = (
        dataclasses.replace(entries[0], readiness=dataclasses.replace(
            entries[0].readiness, reconstruction_version="17.0.0")),
        dataclasses.replace(entries[0], date="2026-08-15", readiness=dataclasses.replace(
            entries[0].readiness, reconstruction_version="18.3.0")),
    )
    with patch("bujji.market_reality_snapshot.dataset_version.build_research_calendar", return_value=mismatched):
        with pytest.raises(ValueError):
            build_dataset_version("2026-08-14", "2026-08-15", historical_store=store)


# --- No raw Reality storage was touched -------------------------------------------
def test_no_backtest_or_strategy_concepts_leaked_into_governance_modules():
    """Structural guard matching this phase's own constraints: neither
    new module may reference PnL, strategy, or backtest concepts."""
    import bujji.market_reality_snapshot.research_calendar as rc
    import bujji.market_reality_snapshot.dataset_version as dv
    for mod in (rc, dv):
        src = Path(mod.__file__).read_text().lower()
        for forbidden in ("pnl", "strategy_", "backtest_engine", "place_order"):
            assert forbidden not in src, f"{mod.__name__} unexpectedly references {forbidden!r}"
