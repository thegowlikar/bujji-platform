"""Phase 17J.1-revisit — IntradayStructureRecord / IntradayStructureCatalog tests.

Also guards the boundary decision this phase made explicit: RealityMemoryEvent
must remain Reality-only, never gaining structure fields.
"""
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from bujji.historical_reality.capture import build_historical_observation
from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_understanding.structure import IntradayStructureCatalog, IntradayStructureRecord
from bujji.reality_memory.models import RealityMemoryEvent


def _write_session(store, instrument_identity, date, n=6):
    for i in range(n):
        store.write(build_historical_observation(
            instrument_identity=instrument_identity, instrument_type="SPOT",
            resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE,
            timestamp=f"{date}T09:{15+5*i:02d}:00+05:30",
            payload={"open": 100.0 + i, "high": 100.0 + i, "low": 100.0 + i,
                     "close": 100.0 + i, "volume": None},
            source="fyers_historical", access_method="direct_sdk_fyers_historical_intraday_rest",
            source_epoch=i, source_symbol=instrument_identity,
            raw_artifact_ref="x", ingestion_run_id="RUN-x",
            retrieved_at=f"{date}T09:{15+5*i:02d}:00+05:30",
            certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
        ))


# --- Boundary guard: RealityMemoryEvent must stay Reality-only -------------------
def test_reality_memory_event_has_no_structure_fields():
    """Structural guard for the decision made this phase: intraday
    structure lives in market_understanding, never merged into
    RealityMemoryEvent."""
    fields = RealityMemoryEvent.__dataclass_fields__
    forbidden = ("trend_state", "swing_state", "support_state", "resistance_state",
                 "breakout_state", "breakdown_state", "structure_state")
    for name in forbidden:
        assert name not in fields


# --- IntradayStructureCatalog -----------------------------------------------------
def test_catalog_returns_a_record_for_a_populated_session():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        _write_session(store, "NSE:NIFTY50-INDEX", "2026-08-01")
        catalog = IntradayStructureCatalog(historical_store=store)
        record = catalog.get("NSE:NIFTY50-INDEX", "2026-08-01T09:35:00+05:30")
        assert record is not None
        assert record.instrument_identity == "NSE:NIFTY50-INDEX"
        assert record.price_structure.trend_state is not None
        assert record.market_structure.support_state is not None


def test_catalog_returns_none_for_insufficient_data():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        catalog = IntradayStructureCatalog(historical_store=store)
        record = catalog.get("NSE:NIFTY50-INDEX", "2026-08-01T09:35:00+05:30")
        assert record is None


def test_record_lineage_traces_to_real_observation_ids():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        _write_session(store, "NSE:NIFTY50-INDEX", "2026-08-01")
        catalog = IntradayStructureCatalog(historical_store=store)
        record = catalog.get("NSE:NIFTY50-INDEX", "2026-08-01T09:35:00+05:30")
        assert len(record.supporting_observation_ids) > 0
        for obs_id in record.supporting_observation_ids:
            assert obs_id.startswith("OBS-")


def test_to_dict_exposes_understanding_tier_fields_not_reality_fields():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        _write_session(store, "NSE:NIFTY50-INDEX", "2026-08-01")
        catalog = IntradayStructureCatalog(historical_store=store)
        record = catalog.get("NSE:NIFTY50-INDEX", "2026-08-01T09:35:00+05:30")
        d_out = record.to_dict()
        assert "trend_state" in d_out and "support_state" in d_out
        # Never a raw OHLC field -- that stays in RealityMemoryEvent, never duplicated here.
        assert "spot_close" not in d_out and "open" not in d_out


def test_catalog_is_read_only_never_mutates_the_store():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        _write_session(store, "NSE:NIFTY50-INDEX", "2026-08-01")
        before = store.count()
        catalog = IntradayStructureCatalog(historical_store=store)
        catalog.get("NSE:NIFTY50-INDEX", "2026-08-01T09:35:00+05:30")
        catalog.get("NSE:NIFTY50-INDEX", "2026-08-01T09:35:00+05:30")
        assert store.count() == before


def test_real_data_2020_03_23_produces_a_full_lineage_record():
    # `if not real_db.exists(): return` was TWO defects in one line. It made an
    # absent store look like a PASS rather than a skip, and it treated
    # existence as availability -- but constructing HistoricalObservationStore
    # at this path creates an empty schema'd file, so a sibling test's leftover
    # made this run against nothing and fail on the second invocation of an
    # unchanged tree.
    import pytest

    from tests._real_store_guard import production_store_has_data, skip_reason

    real_db = _REPO_ROOT / "data" / "historical_reality" / "normalized" / "historical_observations.db"
    if not production_store_has_data(real_db):
        pytest.skip(skip_reason(real_db))
    store = HistoricalObservationStore(str(real_db))
    catalog = IntradayStructureCatalog(historical_store=store)
    record = catalog.get("NSE:NIFTY50-INDEX", "2020-03-23T15:25:00+05:30")
    assert record is not None
    assert record.price_structure.structure_integrity == "COHERENT"
    assert len(record.supporting_observation_ids) > 0
