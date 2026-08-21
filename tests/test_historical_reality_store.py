"""Phase 17H.3/17H.4 — HistoricalObservationStore.

Same three-outcome discipline test shape already proven for
RawObservationStore/CandleStore: accept, idempotent no-op, conflict.
"""
import pytest

from bujji.historical_reality.capture import build_historical_observation
from bujji.historical_reality.models import IngestionRun, RUN_STATUS_OK
from bujji.historical_reality.store import (
    ConflictingHistoricalObservationError,
    HistoricalObservationStore,
)
from bujji.market_observation import taxonomy as moc_taxonomy

NOW = "2026-08-13T16:52:00+05:30"


def _obs(timestamp="1998-05-04T09:15:00+05:30", close=1185.15, run_id="RUN-1"):
    return build_historical_observation(
        instrument_identity="NSE:NIFTY50-INDEX", instrument_type="SPOT",
        resolution=moc_taxonomy.RESOLUTION_DAILY, timestamp=timestamp,
        payload={"open": 1159.8, "high": 1185.75, "low": 1159.8, "close": close, "volume": None},
        source="fyers_historical", access_method="direct_sdk_fyers_historical_rest",
        source_epoch=894240000, source_symbol="NSE:NIFTY50-INDEX",
        raw_artifact_ref="x.json", ingestion_run_id=run_id, retrieved_at=NOW,
        certification_status="CERTIFIED_AVAILABLE",
    )


# --- Write / accept ------------------------------------------------------------
def test_write_accepts_a_new_observation(tmp_path):
    store = HistoricalObservationStore(tmp_path / "test.db")
    assert store.write(_obs()) is True
    assert store.count() == 1
    assert store.holds(_obs().observation_id) is True


def test_write_is_idempotent_for_identical_content(tmp_path):
    store = HistoricalObservationStore(tmp_path / "test.db")
    obs = _obs()
    assert store.write(obs) is True
    assert store.write(obs) is False  # Idempotent no-op, not a duplicate.
    assert store.count() == 1


def test_write_rejects_conflicting_content_under_same_natural_key(tmp_path):
    """Same (instrument, resolution, timestamp, source) but a DIFFERENT
    real value -- a source correction -- must be surfaced, never
    silently overwritten (PHASE_17H3 §3.1)."""
    store = HistoricalObservationStore(tmp_path / "test.db")
    store.write(_obs(close=1185.15))
    with pytest.raises(ConflictingHistoricalObservationError):
        store.write(_obs(close=9999.0))
    assert store.count() == 1  # The original fact is untouched.


def test_a_different_date_is_not_a_conflict(tmp_path):
    store = HistoricalObservationStore(tmp_path / "test.db")
    store.write(_obs(timestamp="1998-05-04T09:15:00+05:30"))
    store.write(_obs(timestamp="1998-05-05T09:15:00+05:30"))
    assert store.count() == 2


# --- Restart survival ------------------------------------------------------------
def test_fresh_store_instance_rehydrates_and_still_detects_conflicts(tmp_path):
    path = tmp_path / "test.db"
    store1 = HistoricalObservationStore(path)
    store1.write(_obs(close=1185.15))

    store2 = HistoricalObservationStore(path)  # Real process-restart simulation.
    assert store2.count() == 1
    assert store2.holds(_obs().observation_id) is True
    with pytest.raises(ConflictingHistoricalObservationError):
        store2.write(_obs(close=9999.0))


# --- Range queries ------------------------------------------------------------
def test_range_query_returns_only_the_requested_window(tmp_path):
    store = HistoricalObservationStore(tmp_path / "test.db")
    store.write(_obs(timestamp="1998-05-04T09:15:00+05:30"))
    store.write(_obs(timestamp="2020-01-01T09:15:00+05:30"))

    rows = store.range("NSE:NIFTY50-INDEX", moc_taxonomy.RESOLUTION_DAILY,
                        "1998-01-01T00:00:00+05:30", "1999-01-01T00:00:00+05:30")
    assert len(rows) == 1
    assert rows[0].observation.identity.timestamp == "1998-05-04T09:15:00+05:30"


# --- IngestionRun ------------------------------------------------------------
def test_ingestion_run_is_recorded_and_queryable(tmp_path):
    store = HistoricalObservationStore(tmp_path / "test.db")
    run = IngestionRun(
        ingestion_run_id="RUN-1", source="fyers_historical", instrument="NSE:NIFTY50-INDEX",
        resolution=moc_taxonomy.RESOLUTION_DAILY, range_from="1998-01-01", range_to="1998-12-31",
        started_at=NOW, status=RUN_STATUS_OK, rows_returned=170, rows_accepted=170,
        raw_artifact_path="x.json",
    )
    store.record_ingestion_run(run)
    runs = store.ingestion_runs_for("NSE:NIFTY50-INDEX")
    assert len(runs) == 1
    assert runs[0].rows_accepted == 170
