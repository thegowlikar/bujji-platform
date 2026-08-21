"""Phase 18.5 -- Research Session Readiness contract tests."""
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import pytest

from bujji.historical_reality.capture import build_historical_observation
from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_reality_snapshot.builder import OPTIONS_UNDERLYING
from bujji.market_reality_snapshot.models import RESOLUTION_DAILY, RESOLUTION_FIVE_MINUTE
from bujji.market_reality_snapshot.readiness import (
    ResearchSessionReadiness,
    check_research_session_readiness,
)

SPOT_SYMBOL = "NSE:NIFTY50-INDEX"
VIX_SYMBOL = "NSE:INDIAVIX-INDEX"
FUTURES_SYMBOL = "NIFTY_FUT_CONTINUOUS"


def _obs(identity, instrument_type, resolution, timestamp, payload, value_kind=moc_taxonomy.VALUE_KIND_OHLC,
         cert_status="CERTIFIED_AVAILABLE", cert_ref="ref-1"):
    return build_historical_observation(
        instrument_identity=identity, instrument_type=instrument_type,
        resolution=resolution, timestamp=timestamp, payload=payload,
        source="fyers", access_method="test_access_method",
        source_epoch=1755000000, source_symbol=identity,
        raw_artifact_ref="", ingestion_run_id="RUN-test", retrieved_at=timestamp,
        certification_status=cert_status, certification_ref=cert_ref,
        value_kind=value_kind,
    )


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as d:
        yield HistoricalObservationStore(str(Path(d) / "hist.db"))


def test_all_absent_reports_incomplete_with_all_four_missing(store):
    r = check_research_session_readiness("2026-08-14", historical_store=store,
                                          resolution=RESOLUTION_FIVE_MINUTE, as_of_time="2026-08-14T09:20:00+05:30")
    assert not r.is_complete
    assert r.missing == ("spot", "futures", "vix", "options")
    assert r.completeness == "EMPTY"
    assert r.certified_lineage_available is False
    assert r.depth_available is None


def test_full_session_reports_complete(store):
    ts = "2026-08-14T09:20:00+05:30"
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 1, "high": 2, "low": 0.5, "close": 1.5}))
    store.write(_obs(FUTURES_SYMBOL, "FUTURE", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 1, "high": 2, "low": 0.5, "close": 1.5}))
    store.write(_obs(VIX_SYMBOL, "INDEX", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 12, "high": 12.5, "low": 11.5, "close": 12.1}))
    store.write(_obs(f"{OPTIONS_UNDERLYING}|2026-08-18|21800|CE", "OPTION", RESOLUTION_FIVE_MINUTE, ts,
                      {"ltp": 250.0}, value_kind=moc_taxonomy.VALUE_KIND_MAPPING))

    r = check_research_session_readiness("2026-08-14", historical_store=store,
                                          resolution=RESOLUTION_FIVE_MINUTE, as_of_time=ts)
    assert r.is_complete
    assert r.missing == ()
    assert r.completeness == "COMPLETE"
    assert r.certified_lineage_available is True
    assert len(r.fingerprint) == 64


def test_partial_session_reports_exactly_which_component_is_missing(store):
    ts = "2026-08-14T09:20:00+05:30"
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 1, "high": 2, "low": 0.5, "close": 1.5}))
    store.write(_obs(FUTURES_SYMBOL, "FUTURE", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 1, "high": 2, "low": 0.5, "close": 1.5}))
    store.write(_obs(VIX_SYMBOL, "INDEX", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 12, "high": 12.5, "low": 11.5, "close": 12.1}))
    # No options written.
    r = check_research_session_readiness("2026-08-14", historical_store=store,
                                          resolution=RESOLUTION_FIVE_MINUTE, as_of_time=ts)
    assert not r.is_complete
    assert r.missing == ("options",)
    assert r.spot_available and r.futures_available and r.vix_available
    assert not r.options_available


def test_daily_resolution_readiness_never_finds_options(store):
    store.write(_obs(SPOT_SYMBOL, "SPOT", moc_taxonomy.RESOLUTION_DAILY, "2018-01-05T09:15:00+05:30",
                      {"open": 1, "high": 2, "low": 0.5, "close": 1.5}))
    r = check_research_session_readiness("2018-01-05", historical_store=store, resolution=RESOLUTION_DAILY)
    assert r.spot_available
    assert not r.options_available  # honest: options have never existed at DAILY resolution.


def test_readiness_fingerprint_matches_underlying_snapshot_fingerprint(store):
    ts = "2026-08-14T09:20:00+05:30"
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 1, "high": 2, "low": 0.5, "close": 1.5}))
    from bujji.market_reality_snapshot.builder import build_market_reality_snapshot
    snap = build_market_reality_snapshot("2026-08-14", historical_store=store,
                                          resolution=RESOLUTION_FIVE_MINUTE, as_of_time=ts)
    r = check_research_session_readiness("2026-08-14", historical_store=store,
                                          resolution=RESOLUTION_FIVE_MINUTE, as_of_time=ts)
    assert r.fingerprint == snap.fingerprint()


def test_readiness_never_raises_never_fabricates(store):
    """No exception for a totally empty store/date; never a guessed
    True where data is absent."""
    r = check_research_session_readiness("1990-01-01", historical_store=store, resolution=RESOLUTION_DAILY)
    assert r.is_complete is False
    assert r.spot_available is False


def test_to_dict_is_json_shaped_and_stable():
    import json
    r = ResearchSessionReadiness(
        date="2026-08-14", resolution="FIVE_MINUTE", as_of_time="2026-08-14T09:20:00+05:30",
        spot_available=True, futures_available=True, vix_available=True, options_available=True,
        depth_available=None, certified_lineage_available=True, completeness="COMPLETE",
        fingerprint="a" * 64, reconstruction_version="18.3.0",
    )
    d = r.to_dict()
    json.dumps(d)  # must not raise
    assert d["is_complete"] is True
    assert d["missing"] == []
