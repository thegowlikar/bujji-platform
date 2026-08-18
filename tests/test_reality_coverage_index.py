"""Phase 17I.4 — Reality Coverage Index tests."""
import datetime
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from bujji.historical_reality.capture import build_historical_observation
from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_reality import taxonomy as reality_taxonomy
from bujji.market_reality.capture import build_raw_observation
from bujji.market_reality.certification import CertificationGate, StaticCertificationGate
from bujji.market_reality.store import RawObservationStore
from bujji.market_reality_snapshot.builder import (
    FUTURES_CONTINUOUS_IDENTITY, SPOT_SYMBOL, VIX_SYMBOL,
)
from bujji.reality_coverage.index import RealityCoverageIndex


def _daily_obs(instrument_identity, instrument_type, date, access_method="direct_sdk_fyers_historical_rest"):
    return build_historical_observation(
        instrument_identity=instrument_identity, instrument_type=instrument_type,
        resolution=moc_taxonomy.RESOLUTION_DAILY, timestamp=f"{date}T09:15:00+05:30",
        payload={"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": None},
        source="fyers_historical", access_method=access_method,
        source_epoch=1, source_symbol=instrument_identity,
        raw_artifact_ref="x", ingestion_run_id="RUN-x", retrieved_at=f"{date}T09:15:00+05:30",
        certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
    )


def _five_min_obs(instrument_identity, instrument_type, timestamp,
                   access_method="direct_sdk_fyers_historical_intraday_rest"):
    return build_historical_observation(
        instrument_identity=instrument_identity, instrument_type=instrument_type,
        resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE, timestamp=timestamp,
        payload={"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": None},
        source="fyers_historical", access_method=access_method,
        source_epoch=1, source_symbol=instrument_identity,
        raw_artifact_ref="x", ingestion_run_id="RUN-x", retrieved_at=timestamp,
        certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
    )


def _depth_raw_obs(capture_ts):
    payload = {
        "bids": [{"price": 100.0, "volume": 10, "order_count": 1}],
        "asks": [{"price": 100.5, "volume": 10, "order_count": 1}],
        "open_interest": 1000, "prior_day_open_interest": 990,
        "total_buy_quantity": 1000, "total_sell_quantity": 900, "last_price": 100.2,
    }
    return build_raw_observation(
        kind="MARKET_DEPTH", instrument=FUTURES_CONTINUOUS_IDENTITY,
        instrument_type=reality_taxonomy.INSTRUMENT_FUTURE, payload=payload,
        source="fyers", access_method="direct_sdk_fyers_broker_py_depth",
        capture_timestamp=capture_ts,
        identity_fields={"expiry": "2026-08-27", "source_symbol": "NSE:NIFTY26AUGFUT"},
        certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
    )


def test_resolve_reports_full_coverage_for_a_fully_populated_date():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        date = "2026-08-01"
        store.write(_daily_obs(SPOT_SYMBOL, "SPOT", date))
        store.write(_daily_obs(VIX_SYMBOL, "INDEX", date))
        store.write(_daily_obs(FUTURES_CONTINUOUS_IDENTITY, "FUTURE", date))
        for hh, mm in (("09", "15"), ("09", "20"), ("09", "25")):
            ts = f"{date}T{hh}:{mm}:00+05:30"
            store.write(_five_min_obs(SPOT_SYMBOL, "SPOT", ts))

        gate = StaticCertificationGate(reality_taxonomy.CERTIFIED_AVAILABLE)
        idx = RealityCoverageIndex(historical_store=store, certification_gate=gate)
        result = idx.resolve(date)

        assert result["date"] == date
        assert result["daily"]["availability"] == {"spot": True, "futures": True, "vix": True}
        assert result["daily"]["completeness"] == "COMPLETE"
        assert result["intraday"]["spot"]["available"] is True
        assert result["intraday"]["spot"]["observation_count"] == 3
        assert result["intraday"]["futures"]["available"] is False
        assert result["intraday"]["futures"]["observation_count"] == 0


def test_resolve_reports_honest_absence_for_an_empty_date():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        gate = StaticCertificationGate(reality_taxonomy.CERTIFICATION_MISSING)
        idx = RealityCoverageIndex(historical_store=store, certification_gate=gate)
        result = idx.resolve("1900-01-01")

        assert result["daily"]["availability"] == {"spot": False, "futures": False, "vix": False}
        assert result["daily"]["completeness"] == "EMPTY"
        for instrument in ("spot", "futures", "vix"):
            assert result["intraday"][instrument]["available"] is False
            assert result["intraday"][instrument]["observation_count"] == 0
        assert result["microstructure"]["futures_depth"]["available"] is False


def test_intraday_count_only_counts_the_requested_calendar_date():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        store.write(_five_min_obs(SPOT_SYMBOL, "SPOT", "2026-08-01T09:15:00+05:30"))
        store.write(_five_min_obs(SPOT_SYMBOL, "SPOT", "2026-08-02T09:15:00+05:30"))

        gate = StaticCertificationGate(reality_taxonomy.CERTIFIED_AVAILABLE)
        idx = RealityCoverageIndex(historical_store=store, certification_gate=gate)
        result = idx.resolve("2026-08-01")
        assert result["intraday"]["spot"]["observation_count"] == 1


def test_daily_and_intraday_are_independently_reported_not_conflated():
    """A date with intraday coverage but no daily bar (or vice versa)
    must report each honestly -- never inferring one from the other."""
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        store.write(_five_min_obs(SPOT_SYMBOL, "SPOT", "2026-08-01T09:15:00+05:30"))
        # No daily bar written for this date.
        gate = StaticCertificationGate(reality_taxonomy.CERTIFIED_AVAILABLE)
        idx = RealityCoverageIndex(historical_store=store, certification_gate=gate)
        result = idx.resolve("2026-08-01")
        assert result["daily"]["availability"]["spot"] is False
        assert result["intraday"]["spot"]["available"] is True


def test_microstructure_coverage_counts_only_market_depth_kind():
    with tempfile.TemporaryDirectory() as d:
        hist_store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        gate = StaticCertificationGate(reality_taxonomy.CERTIFIED_AVAILABLE)
        live_dir = Path(d) / "layer0"
        cert_gate = CertificationGate(str(Path(d) / "certs"))
        live_store = RawObservationStore(str(live_dir), cert_gate, session_id="test")

        depth_obs = _depth_raw_obs("2026-08-01T09:20:00+05:30")
        result_write = live_store.append(depth_obs, now="2026-08-01T09:20:01+05:30")
        assert result_write.outcome == reality_taxonomy.OUTCOME_REJECTED  # No cert on disk yet.

        # Certify, then write for real.
        cert_dir = Path(d) / "certs"
        cert_dir.mkdir(parents=True, exist_ok=True)
        (cert_dir / "fyers_nifty_future_depth_certification_20260801.json").write_text(
            '{"timestamp": "2026-08-01T09:00:00+05:30", "access_method": '
            '"direct_sdk_fyers_broker_py_depth", "instrument": "NIFTY_FUTURES", '
            '"validation_result": "CERTIFIED_AVAILABLE"}'
        )
        live_store_2 = RawObservationStore(str(live_dir), cert_gate, session_id="test")
        result_write_2 = live_store_2.append(depth_obs, now="2026-08-01T09:20:02+05:30")
        assert result_write_2.outcome == reality_taxonomy.OUTCOME_ACCEPTED

        idx = RealityCoverageIndex(historical_store=hist_store, certification_gate=gate,
                                    live_store=live_store_2)
        result = idx.resolve("2026-08-01")
        assert result["microstructure"]["futures_depth"]["available"] is True
        assert result["microstructure"]["futures_depth"]["observation_count"] == 1


def test_certification_section_reports_live_status_not_cached():
    with tempfile.TemporaryDirectory() as d:
        cert_dir = Path(d) / "certs"
        cert_dir.mkdir(parents=True, exist_ok=True)
        hist_store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        gate = CertificationGate(str(cert_dir))
        idx = RealityCoverageIndex(historical_store=hist_store, certification_gate=gate)

        result_before = idx.resolve("2026-08-01")
        assert result_before["certification"]["direct_sdk_fyers_broker_py_depth"]["status"] \
            == reality_taxonomy.CERTIFICATION_MISSING

        (cert_dir / "fyers_nifty_future_depth_certification_20260801.json").write_text(
            '{"timestamp": "2026-08-01T09:00:00+05:30", "access_method": '
            '"direct_sdk_fyers_broker_py_depth", "instrument": "NIFTY_FUTURES", '
            '"validation_result": "CERTIFIED_AVAILABLE"}'
        )
        result_after = idx.resolve("2026-08-01")
        assert result_after["certification"]["direct_sdk_fyers_broker_py_depth"]["status"] \
            == reality_taxonomy.CERTIFIED_AVAILABLE


def test_certification_section_covers_all_tracked_access_methods():
    with tempfile.TemporaryDirectory() as d:
        hist_store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        gate = StaticCertificationGate(reality_taxonomy.CERTIFIED_AVAILABLE)
        idx = RealityCoverageIndex(historical_store=hist_store, certification_gate=gate)
        result = idx.resolve("2026-08-01")
        expected = {
            "direct_sdk_fyers_historical_rest",
            "direct_sdk_fyers_historical_intraday_rest",
            "direct_sdk_fyers_broker_py",
            "direct_sdk_fyers_broker_py_depth",
        }
        assert expected.issubset(result["certification"].keys())


def test_resolve_never_mutates_any_store():
    """A read-only index must not write anything -- counts before and
    after resolve() must be identical."""
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        store.write(_daily_obs(SPOT_SYMBOL, "SPOT", "2026-08-01"))
        before = store.count()
        gate = StaticCertificationGate(reality_taxonomy.CERTIFIED_AVAILABLE)
        idx = RealityCoverageIndex(historical_store=store, certification_gate=gate)
        idx.resolve("2026-08-01")
        idx.resolve("2026-08-01")
        after = store.count()
        assert before == after == 1
