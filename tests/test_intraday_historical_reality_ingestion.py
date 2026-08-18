"""Phase 17H.9 — Intraday Historical Reality Ingestion tests.

Covers the 10 areas called for in the phase spec: identity uniqueness
across resolution/timestamp, resolution separation, idempotent
ingestion, conflict detection, chunking (100-day), certification
isolation (distinct access_method), validator rejection, and the
real earliest-date boundaries. Mirrors
test_ingest_vix_and_futures_daily_historical.py's load-by-path pattern.
"""
import datetime
import importlib.util
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from bujji.historical_reality.capture import build_historical_observation
from bujji.historical_reality.store import (
    ConflictingHistoricalObservationError, HistoricalObservationStore,
)
from bujji.market_observation import taxonomy as moc_taxonomy


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _REPO_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


spot_ingest = _load("ingest_nifty_spot_intraday_historical", "ingest_nifty_spot_intraday_historical.py")
vix_ingest = _load("ingest_india_vix_intraday_historical", "ingest_india_vix_intraday_historical.py")
fut_ingest = _load("ingest_nifty_futures_intraday_historical", "ingest_nifty_futures_intraday_historical.py")

spot_cert = _load("certify_fyers_historical_spot_intraday_access", "certify_fyers_historical_spot_intraday_access.py")
vix_cert = _load("certify_fyers_historical_vix_intraday_access", "certify_fyers_historical_vix_intraday_access.py")
fut_cert = _load("certify_fyers_historical_futures_intraday_access", "certify_fyers_historical_futures_intraday_access.py")

daily_spot_ingest = _load("ingest_nifty_spot_daily_historical", "ingest_nifty_spot_daily_historical.py")


# --- 1. Identity uniqueness across resolution/timestamp (the "architectural rule") ---
def test_daily_and_5min_same_date_get_distinct_observation_ids():
    daily = build_historical_observation(
        instrument_identity="NSE:NIFTY50-INDEX", instrument_type="SPOT",
        resolution=moc_taxonomy.RESOLUTION_DAILY, timestamp="2020-03-23T09:15:00+05:30",
        payload={"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": None},
        source="fyers_historical", access_method="direct_sdk_fyers_historical_rest",
        source_epoch=1584936900, source_symbol="NSE:NIFTY50-INDEX",
        raw_artifact_ref="x", ingestion_run_id="RUN-x", retrieved_at="2020-03-23T09:15:00+05:30",
        certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
    )
    five_min = build_historical_observation(
        instrument_identity="NSE:NIFTY50-INDEX", instrument_type="SPOT",
        resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE, timestamp="2020-03-23T09:20:00+05:30",
        payload={"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": None},
        source="fyers_historical", access_method="direct_sdk_fyers_historical_intraday_rest",
        source_epoch=1584937200, source_symbol="NSE:NIFTY50-INDEX",
        raw_artifact_ref="x", ingestion_run_id="RUN-x", retrieved_at="2020-03-23T09:20:00+05:30",
        certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
    )
    assert daily.observation_id != five_min.observation_id


def test_two_different_5min_timestamps_same_date_get_distinct_ids():
    def _mk(ts, epoch):
        return build_historical_observation(
            instrument_identity="NSE:NIFTY50-INDEX", instrument_type="SPOT",
            resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE, timestamp=ts,
            payload={"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": None},
            source="fyers_historical", access_method="direct_sdk_fyers_historical_intraday_rest",
            source_epoch=epoch, source_symbol="NSE:NIFTY50-INDEX",
            raw_artifact_ref="x", ingestion_run_id="RUN-x", retrieved_at=ts,
            certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
        )
    a = _mk("2020-03-23T09:20:00+05:30", 1584937200)
    b = _mk("2020-03-23T09:25:00+05:30", 1584937500)
    assert a.observation_id != b.observation_id


def test_identical_5min_observation_is_deterministic():
    def _mk():
        return build_historical_observation(
            instrument_identity="NSE:NIFTY50-INDEX", instrument_type="SPOT",
            resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE, timestamp="2020-03-23T09:20:00+05:30",
            payload={"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": None},
            source="fyers_historical", access_method="direct_sdk_fyers_historical_intraday_rest",
            source_epoch=1584937200, source_symbol="NSE:NIFTY50-INDEX",
            raw_artifact_ref="x", ingestion_run_id="RUN-x", retrieved_at="2020-03-23T09:20:00+05:30",
            certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
        )
    assert _mk().observation_id == _mk().observation_id


# --- 2. Resolution constant exists and is used correctly -------------------------
def test_resolution_five_minute_exists_and_used_by_all_three_ingestion_scripts():
    assert moc_taxonomy.RESOLUTION_FIVE_MINUTE == "FIVE_MINUTE"
    for mod in (spot_ingest, vix_ingest, fut_ingest):
        assert "moc_taxonomy.RESOLUTION_FIVE_MINUTE" in Path(mod.__file__).read_text()


def test_fyers_resolution_param_is_5_not_D():
    assert spot_ingest.FYERS_RESOLUTION == "5"
    assert vix_ingest.FYERS_RESOLUTION == "5"
    assert fut_ingest.FYERS_RESOLUTION == "5"


# --- 3. Chunking respects the real 100-day intraday limit -------------------------
def test_spot_chunks_respect_100_day_limit():
    chunks = list(spot_ingest._chunks(datetime.date(2017, 7, 17), datetime.date(2026, 8, 13),
                                       spot_ingest.CHUNK_DAYS))
    assert spot_ingest.CHUNK_DAYS == 100
    for s, e in chunks:
        assert (e - s).days + 1 <= 100


def test_vix_chunks_respect_100_day_limit():
    chunks = list(vix_ingest._chunks(datetime.date(2017, 7, 17), datetime.date(2026, 8, 13),
                                      vix_ingest.CHUNK_DAYS))
    assert vix_ingest.CHUNK_DAYS == 100
    for s, e in chunks:
        assert (e - s).days + 1 <= 100


def test_futures_chunks_respect_100_day_limit():
    chunks = list(fut_ingest._chunks(datetime.date(2018, 1, 1), datetime.date(2026, 8, 13),
                                      fut_ingest.CHUNK_DAYS))
    assert fut_ingest.CHUNK_DAYS == 100
    for s, e in chunks:
        assert (e - s).days + 1 <= 100


def test_daily_scripts_unaffected_still_use_366_day_chunks():
    """Regression guard: 17H.9 must not have modified daily's chunk size."""
    assert daily_spot_ingest.CHUNK_DAYS == 366


# --- 4. Certification isolation: distinct access_method, no collision ------------
def test_intraday_access_method_is_distinct_from_daily_and_live():
    intraday_am = spot_ingest.ACCESS_METHOD
    daily_am = daily_spot_ingest.ACCESS_METHOD
    assert intraday_am == "direct_sdk_fyers_historical_intraday_rest"
    assert intraday_am != daily_am
    assert intraday_am != "direct_sdk_fyers_broker_py"


def test_all_three_intraday_ingestion_scripts_share_the_same_new_access_method():
    assert spot_ingest.ACCESS_METHOD == vix_ingest.ACCESS_METHOD == fut_ingest.ACCESS_METHOD
    assert spot_ingest.ACCESS_METHOD == spot_cert.ACCESS_METHOD
    assert vix_ingest.ACCESS_METHOD == vix_cert.ACCESS_METHOD
    assert fut_ingest.ACCESS_METHOD == fut_cert.ACCESS_METHOD


def test_intraday_cert_artifact_names_are_distinct_from_daily():
    assert spot_cert.ARTIFACT_NAME != "fyers_nifty_spot_historical_certification"
    assert "intraday" in spot_cert.ARTIFACT_NAME
    assert "intraday" in vix_cert.ARTIFACT_NAME
    assert "intraday" in fut_cert.ARTIFACT_NAME


# --- 5. Real earliest-date boundaries (live-bisected, Phase 17H.8/17H.9) ---------
def test_spot_intraday_default_start_is_2017_07_17_not_daily_1998():
    assert spot_ingest.DEFAULT_START == datetime.date(2017, 7, 17)
    assert spot_ingest.DEFAULT_START != daily_spot_ingest.DEFAULT_START


def test_vix_intraday_default_start_matches_spot_same_platform_cutoff():
    """Real finding: spot and VIX intraday share the identical real
    epoch cutoff -- strong evidence of one platform-wide boundary."""
    assert vix_ingest.DEFAULT_START == spot_ingest.DEFAULT_START == datetime.date(2017, 7, 17)


def test_futures_intraday_default_start_brackets_continuous_series_origin():
    assert fut_ingest.DEFAULT_START == datetime.date(2018, 1, 1)
    assert fut_ingest.DEFAULT_START > datetime.date(2017, 12, 31)


# --- 6. Futures identity rule preserved at intraday resolution -------------------
def test_futures_intraday_instrument_identity_is_never_the_request_symbol():
    assert fut_ingest.INSTRUMENT_IDENTITY == "NIFTY_FUT_CONTINUOUS"
    assert fut_ingest.INSTRUMENT_IDENTITY != "NSE:NIFTY26AUGFUT"
    assert fut_ingest.CONTINUITY_METHOD == "fyers_cont_flag_1"


# --- 7. Validator rejection (same discipline as daily, real bad-row shape) -------
def test_validate_row_rejects_impossible_ohlc():
    row = [1500262800, 100.0, 90.0, 95.0, 92.0, 0]  # high < open
    assert spot_ingest._validate_row(row) is not None


def test_validate_row_rejects_the_real_negative_sentinel_pattern():
    """Same -1.0 sentinel artifact class found live in 17H.6's VIX
    daily ingestion -- the validator must reject it at intraday
    resolution too, not just daily."""
    row = [1613088000, -1.0, -1.0, -1.0, 23.05, 0]
    assert vix_ingest._validate_row(row) is not None


def test_validate_row_accepts_a_plausible_intraday_candle():
    row = [1500262800, 9950.5, 9960.0, 9945.0, 9955.0, 1200]
    assert spot_ingest._validate_row(row) is None


# --- 8. Idempotent ingestion + conflict detection (store-level, reused unmodified) -
def _mk_obs(store_kwargs):
    return build_historical_observation(**store_kwargs)


def test_idempotent_rewrite_of_identical_intraday_observation_is_a_no_op():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "test.db"))
        kwargs = dict(
            instrument_identity="NSE:NIFTY50-INDEX", instrument_type="SPOT",
            resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE, timestamp="2020-03-23T09:20:00+05:30",
            payload={"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": None},
            source="fyers_historical", access_method="direct_sdk_fyers_historical_intraday_rest",
            source_epoch=1584937200, source_symbol="NSE:NIFTY50-INDEX",
            raw_artifact_ref="x", ingestion_run_id="RUN-a", retrieved_at="2020-03-23T09:20:00+05:30",
            certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
        )
        store.write(_mk_obs(kwargs))
        store.write(_mk_obs(kwargs))  # identical re-write -- must be a silent no-op
        assert store.count("NSE:NIFTY50-INDEX") == 1


def test_conflicting_intraday_observation_under_same_key_is_rejected():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "test.db"))
        base_kwargs = dict(
            instrument_identity="NSE:NIFTY50-INDEX", instrument_type="SPOT",
            resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE, timestamp="2020-03-23T09:20:00+05:30",
            source="fyers_historical", access_method="direct_sdk_fyers_historical_intraday_rest",
            source_epoch=1584937200, source_symbol="NSE:NIFTY50-INDEX",
            raw_artifact_ref="x", ingestion_run_id="RUN-a", retrieved_at="2020-03-23T09:20:00+05:30",
            certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
        )
        store.write(_mk_obs({**base_kwargs, "payload": {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": None}}))
        with __import__("pytest").raises(ConflictingHistoricalObservationError):
            store.write(_mk_obs({**base_kwargs, "payload": {"open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0, "volume": None}}))


def test_daily_and_intraday_rows_for_the_same_date_coexist_without_collision():
    """Same date, different resolution -- both must be independently
    storable, proving resolution is genuinely part of the natural key."""
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "test.db"))
        daily = build_historical_observation(
            instrument_identity="NSE:NIFTY50-INDEX", instrument_type="SPOT",
            resolution=moc_taxonomy.RESOLUTION_DAILY, timestamp="2020-03-23T09:15:00+05:30",
            payload={"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": None},
            source="fyers_historical", access_method="direct_sdk_fyers_historical_rest",
            source_epoch=1584936900, source_symbol="NSE:NIFTY50-INDEX",
            raw_artifact_ref="x", ingestion_run_id="RUN-d", retrieved_at="2020-03-23T09:15:00+05:30",
            certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
        )
        five_min = build_historical_observation(
            instrument_identity="NSE:NIFTY50-INDEX", instrument_type="SPOT",
            resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE, timestamp="2020-03-23T09:15:00+05:30",
            payload={"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": None},
            source="fyers_historical", access_method="direct_sdk_fyers_historical_intraday_rest",
            source_epoch=1584936900, source_symbol="NSE:NIFTY50-INDEX",
            raw_artifact_ref="x", ingestion_run_id="RUN-i", retrieved_at="2020-03-23T09:15:00+05:30",
            certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
        )
        store.write(daily)
        store.write(five_min)  # must NOT conflict with the daily row despite identical timestamp
        assert store.count("NSE:NIFTY50-INDEX") == 2


# --- 9. Real per-candle timestamps (epoch_to_ist), not a fixed session marker ------
def test_ingestion_scripts_use_epoch_to_ist_not_a_fixed_marker():
    for mod in (spot_ingest, vix_ingest, fut_ingest):
        src = Path(mod.__file__).read_text()
        assert "epoch_to_ist" in src
        assert 'T09:15:00+05:30"' not in src.split("timestamp = ")[-1][:40]


# --- 10. Not market-hours gated, same discipline as every historical script -------
def test_intraday_scripts_are_not_market_hours_gated():
    for mod in (spot_ingest, vix_ingest, fut_ingest, spot_cert, vix_cert, fut_cert):
        assert not hasattr(mod, "within_market_hours")
