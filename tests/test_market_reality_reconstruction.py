"""Phase 17H.7 — Historical Reality Reconstruction Engine.

Isolated fixtures, same pattern as test_market_reality_snapshot.py.
`reconstruct_market_reality()` is a thin view over
`build_market_reality_snapshot()` -- these tests exercise the dict
shape and the rules (never fabricate, preserve identity boundaries,
explicit historical/live origin) specific to Phase 17H.7, not the
underlying combination logic already covered by
test_market_reality_snapshot.py.
"""
import datetime

import pytest

from bujji.historical_reality.capture import build_historical_observation
from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_reality import taxonomy as reality_taxonomy
from bujji.market_reality.capture import build_raw_observation
from bujji.market_reality.certification import StaticCertificationGate
from bujji.market_reality.store import RawObservationStore
from bujji.market_reality_snapshot.reconstruction import (
    ORIGIN_HISTORICAL,
    ORIGIN_LIVE,
    reconstruct_market_reality,
)
from bujji.market_reality_snapshot.models import COMPLETENESS_COMPLETE, COMPLETENESS_EMPTY, COMPLETENESS_PARTIAL

CERT = reality_taxonomy.CERTIFIED_AVAILABLE
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
SPOT_SYMBOL = "NSE:NIFTY50-INDEX"
VIX_SYMBOL = "NSE:INDIAVIX-INDEX"
FUT_IDENTITY = "NIFTY_FUT_CONTINUOUS"
NOW = datetime.datetime(2026, 8, 14, 10, 0, tzinfo=IST)


def _gate():
    return StaticCertificationGate(CERT)


def _hist_obs(identity, itype, date, close, cont_method=None, cert_ref="hist_cert@ts"):
    return build_historical_observation(
        instrument_identity=identity, instrument_type=itype,
        resolution=moc_taxonomy.RESOLUTION_DAILY, timestamp=f"{date}T09:15:00+05:30",
        payload={"open": close - 5, "high": close + 5, "low": close - 10, "close": close, "volume": None},
        source="fyers_historical", access_method="direct_sdk_fyers_historical_rest",
        source_epoch=1000000, source_symbol=identity, raw_artifact_ref="x.json",
        ingestion_run_id="RUN-test", retrieved_at="2026-08-13T16:00:00+05:30",
        certification_status=CERT, certification_ref=cert_ref, continuity_method=cont_method,
    )


def _live_obs(date_time, itype, instrument, payload, identity_fields=None):
    return build_raw_observation(
        kind=reality_taxonomy.KIND_QUOTE, instrument=instrument, instrument_type=itype,
        payload=payload, source="fyers", access_method="direct_sdk_fyers_broker_py",
        capture_timestamp=date_time, event_timestamp=None,
        certification_status=CERT, identity_fields=identity_fields or {},
    )


@pytest.fixture
def hist_store(tmp_path):
    return HistoricalObservationStore(tmp_path / "hist.db")


@pytest.fixture
def live_store(tmp_path):
    return RawObservationStore(tmp_path / "layer0", _gate(), session_id="test-reconstruction")


# --- 1. Full three-source reconstruction ---------------------------------------
def test_full_three_source_reconstruction(hist_store, live_store):
    date = "2020-03-23"
    hist_store.write(_hist_obs(SPOT_SYMBOL, reality_taxonomy.INSTRUMENT_SPOT, date, 7610.25))
    hist_store.write(_hist_obs(FUT_IDENTITY, reality_taxonomy.INSTRUMENT_FUTURE, date, 7581.55,
                                cont_method="fyers_cont_flag_1"))
    hist_store.write(_hist_obs(VIX_SYMBOL, reality_taxonomy.INSTRUMENT_INDEX, date, 71.99))

    result = reconstruct_market_reality(date, historical_store=hist_store, live_store=live_store, now=NOW)

    assert result["completeness"] == COMPLETENESS_COMPLETE
    assert result["availability"] == {"spot": True, "futures": True, "vix": True}
    assert result["spot"]["ohlc"]["close"] == 7610.25
    assert result["futures"]["instrument_identity"] == FUT_IDENTITY
    assert result["futures"]["ohlc"]["close"] == 7581.55
    assert result["vix"]["ohlc"]["close"] == 71.99
    assert len(result["lineage"]) == 3


# --- 2. Spot-only reconstruction -------------------------------------------------
def test_spot_only_reconstruction(hist_store, live_store):
    date = "2005-01-03"
    hist_store.write(_hist_obs(SPOT_SYMBOL, reality_taxonomy.INSTRUMENT_SPOT, date, 2115.0))

    result = reconstruct_market_reality(date, historical_store=hist_store, live_store=live_store, now=NOW)

    assert result["spot"]["available"] is True
    assert result["futures"]["available"] is False
    assert result["vix"]["available"] is False
    assert result["completeness"] == COMPLETENESS_PARTIAL


# --- 3/4. Missing futures / missing VIX -- never fabricated --------------------
def test_missing_futures_is_null_never_fabricated(hist_store, live_store):
    date = "2010-01-04"
    hist_store.write(_hist_obs(SPOT_SYMBOL, reality_taxonomy.INSTRUMENT_SPOT, date, 5232.2))
    hist_store.write(_hist_obs(VIX_SYMBOL, reality_taxonomy.INSTRUMENT_INDEX, date, 23.73))

    result = reconstruct_market_reality(date, historical_store=hist_store, live_store=live_store, now=NOW)

    assert result["futures"] == {
        "available": False, "instrument_identity": None, "observation_id": None,
        "origin": None, "ohlc": None,
    }
    assert result["completeness"] == COMPLETENESS_PARTIAL


def test_missing_vix_is_null_never_fabricated(hist_store, live_store):
    date = "2015-06-01"
    hist_store.write(_hist_obs(SPOT_SYMBOL, reality_taxonomy.INSTRUMENT_SPOT, date, 8200.0))

    result = reconstruct_market_reality(date, historical_store=hist_store, live_store=live_store, now=NOW)
    assert result["vix"]["available"] is False
    assert result["vix"]["ohlc"] is None


# --- 5. Empty date handling --------------------------------------------------------
def test_empty_date_returns_all_absent_not_an_error(hist_store, live_store):
    result = reconstruct_market_reality("1990-01-07", historical_store=hist_store,
                                         live_store=live_store, now=NOW)
    assert result["availability"] == {"spot": False, "futures": False, "vix": False}
    assert result["completeness"] == COMPLETENESS_EMPTY
    assert result["lineage"] == []
    assert result["certification_refs"] == []


def test_reconstruction_never_raises_with_no_live_store_at_all(hist_store):
    """Pure historical reconstruction -- live_store not even supplied."""
    result = reconstruct_market_reality("1990-01-07", historical_store=hist_store)
    assert result["completeness"] == COMPLETENESS_EMPTY


# --- 6. Lineage preservation --------------------------------------------------------
def test_lineage_contains_every_real_observation_id_used(hist_store, live_store):
    date = "2020-03-23"
    spot_obs = _hist_obs(SPOT_SYMBOL, reality_taxonomy.INSTRUMENT_SPOT, date, 7610.25)
    vix_obs = _hist_obs(VIX_SYMBOL, reality_taxonomy.INSTRUMENT_INDEX, date, 71.99)
    hist_store.write(spot_obs)
    hist_store.write(vix_obs)

    result = reconstruct_market_reality(date, historical_store=hist_store, live_store=live_store, now=NOW)
    assert spot_obs.observation_id in result["lineage"]
    assert vix_obs.observation_id in result["lineage"]
    assert result["spot"]["observation_id"] == spot_obs.observation_id


# --- 7. Certification reference preservation ---------------------------------------
def test_certification_refs_are_preserved(hist_store, live_store):
    date = "2005-01-03"
    hist_store.write(_hist_obs(SPOT_SYMBOL, reality_taxonomy.INSTRUMENT_SPOT, date, 2115.0,
                                cert_ref="fyers_nifty_spot_historical_certification_20260813.json@ts"))
    result = reconstruct_market_reality(date, historical_store=hist_store, live_store=live_store, now=NOW)
    assert "fyers_nifty_spot_historical_certification_20260813.json@ts" in result["certification_refs"]


# --- 8. Deterministic reconstruction ------------------------------------------------
def test_reconstruction_is_deterministic_for_a_settled_date(hist_store, live_store):
    date = "2020-03-23"
    hist_store.write(_hist_obs(SPOT_SYMBOL, reality_taxonomy.INSTRUMENT_SPOT, date, 7610.25))

    result_a = reconstruct_market_reality(date, historical_store=hist_store, live_store=live_store, now=NOW)
    result_b = reconstruct_market_reality(date, historical_store=hist_store, live_store=live_store, now=NOW)
    assert result_a == result_b


# --- 9. Serialization round-trip (JSON-shaped output is directly serializable) -----
def test_reconstruction_output_is_json_serializable(hist_store, live_store):
    import json
    date = "2020-03-23"
    hist_store.write(_hist_obs(SPOT_SYMBOL, reality_taxonomy.INSTRUMENT_SPOT, date, 7610.25))
    result = reconstruct_market_reality(date, historical_store=hist_store, live_store=live_store, now=NOW)
    round_tripped = json.loads(json.dumps(result))
    assert round_tripped == result


# --- 10. Restart survival -----------------------------------------------------------
def test_reconstruction_survives_a_fresh_store_instance(tmp_path):
    hist1 = HistoricalObservationStore(tmp_path / "hist.db")
    hist1.write(_hist_obs(SPOT_SYMBOL, reality_taxonomy.INSTRUMENT_SPOT, "2020-03-23", 7610.25))

    hist2 = HistoricalObservationStore(tmp_path / "hist.db")  # Real process-restart simulation.
    result = reconstruct_market_reality("2020-03-23", historical_store=hist2, now=NOW)
    assert result["spot"]["ohlc"]["close"] == 7610.25


# --- 11. Duplicate prevention (exercised through reconstruction, not just the store) -
def test_duplicate_ingestion_does_not_double_lineage(hist_store, live_store):
    date = "2020-03-23"
    obs = _hist_obs(SPOT_SYMBOL, reality_taxonomy.INSTRUMENT_SPOT, date, 7610.25)
    hist_store.write(obs)
    hist_store.write(obs)  # Idempotent no-op -- same fact re-ingested.

    result = reconstruct_market_reality(date, historical_store=hist_store, live_store=live_store, now=NOW)
    assert result["lineage"].count(obs.observation_id) == 1


# --- 12. Conflict detection (real conflict at the store layer, surfaced honestly) ---
def test_conflicting_historical_content_raises_rather_than_silently_reconstructing_wrong_data(hist_store):
    from bujji.historical_reality.store import ConflictingHistoricalObservationError
    date = "2020-03-23"
    hist_store.write(_hist_obs(SPOT_SYMBOL, reality_taxonomy.INSTRUMENT_SPOT, date, 7610.25))
    with pytest.raises(ConflictingHistoricalObservationError):
        hist_store.write(_hist_obs(SPOT_SYMBOL, reality_taxonomy.INSTRUMENT_SPOT, date, 9999.0))
    # The original fact is untouched -- reconstruction still reflects real, uncorrupted data.
    result = reconstruct_market_reality(date, historical_store=hist_store, now=NOW)
    assert result["spot"]["ohlc"]["close"] == 7610.25


# --- Identity boundary preservation (rule 2) ----------------------------------------
def test_futures_never_stored_under_expiry_specific_symbol(hist_store, live_store):
    """Live-sourced futures still resolve to the REAL contract symbol as
    identity (that's correct -- only historical continuous rows use the
    fixed NIFTY_FUT_CONTINUOUS identity); this test confirms a historical
    row is never mistakenly presented as an expiry-specific symbol."""
    date = "2020-03-23"
    hist_store.write(_hist_obs(FUT_IDENTITY, reality_taxonomy.INSTRUMENT_FUTURE, date, 7581.55,
                                cont_method="fyers_cont_flag_1"))
    result = reconstruct_market_reality(date, historical_store=hist_store, live_store=live_store, now=NOW)
    assert result["futures"]["instrument_identity"] == "NIFTY_FUT_CONTINUOUS"
    assert result["futures"]["instrument_identity"] != "NSE:NIFTY26AUGFUT"


# --- Historical vs live origin distinction (rule 3) ---------------------------------
def test_origin_is_explicit_and_never_mixed_silently(hist_store, live_store):
    """Spot from historical, VIX from live on the same date -- both must
    be independently, correctly labeled, never collapsed into one flag."""
    date = "2026-08-13"
    hist_store.write(_hist_obs(SPOT_SYMBOL, reality_taxonomy.INSTRUMENT_SPOT, date, 24395.85))
    live_store.append(_live_obs(f"{date}T13:00:00+05:30", reality_taxonomy.INSTRUMENT_INDEX,
                                 VIX_SYMBOL, {"ltp": 11.39}), now=f"{date}T13:00:00+05:30")

    result = reconstruct_market_reality(date, historical_store=hist_store, live_store=live_store, now=NOW)
    assert result["spot"]["origin"] == ORIGIN_HISTORICAL
    assert result["vix"]["origin"] == ORIGIN_LIVE
