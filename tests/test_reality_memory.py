"""Phase 17J.1 — RealityMemoryEvent / RealityMemoryCatalog tests."""
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from bujji.historical_reality.capture import build_historical_observation
from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_reality_snapshot.builder import (
    FUTURES_CONTINUOUS_IDENTITY, SPOT_SYMBOL, VIX_SYMBOL,
)
from bujji.market_reality_snapshot.models import (
    COMPLETENESS_COMPLETE, COMPLETENESS_EMPTY, COMPLETENESS_PARTIAL,
    FuturesSnapshot, MarketRealitySnapshot, SOURCE_HISTORICAL, SOURCE_LIVE, SpotSnapshot, VixSnapshot,
)
from bujji.reality_memory.catalog import RealityMemoryCatalog
from bujji.reality_memory.models import RealityMemoryEvent


def _daily_obs(instrument_identity, instrument_type, date):
    return build_historical_observation(
        instrument_identity=instrument_identity, instrument_type=instrument_type,
        resolution=moc_taxonomy.RESOLUTION_DAILY, timestamp=f"{date}T09:15:00+05:30",
        payload={"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": None},
        source="fyers_historical", access_method="direct_sdk_fyers_historical_rest",
        source_epoch=1, source_symbol=instrument_identity,
        raw_artifact_ref="x", ingestion_run_id="RUN-x", retrieved_at=f"{date}T09:15:00+05:30",
        certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
    )


# --- RealityMemoryEvent.from_snapshot: pure, literal-facts-only projection ---
def test_from_snapshot_projects_every_literal_field():
    spot = SpotSnapshot(open=100.0, high=105.0, low=95.0, close=102.0, volume=1000.0,
                         source=SOURCE_HISTORICAL, source_observation_ids=("OBS-a",))
    futures = FuturesSnapshot(instrument=FUTURES_CONTINUOUS_IDENTITY, close=103.0,
                               open=101.0, high=106.0, low=96.0, expiry_date=None,
                               source=SOURCE_HISTORICAL, source_observation_ids=("OBS-b",))
    vix = VixSnapshot(close=14.5, open=14.0, high=15.0, low=13.5, change_percent=None,
                       source=SOURCE_HISTORICAL, source_observation_ids=("OBS-c",))
    snap = MarketRealitySnapshot(
        date="2026-08-01", spot=spot, futures=futures, vix=vix,
        completeness=COMPLETENESS_COMPLETE, is_final=True,
        certification_refs=("ref-a", "ref-b", "ref-c"), built_at="2026-08-14T00:00:00+05:30",
    )
    ev = RealityMemoryEvent.from_snapshot(snap)

    assert ev.date == "2026-08-01"
    assert ev.spot_open == 100.0 and ev.spot_close == 102.0 and ev.spot_volume == 1000.0
    assert ev.futures_instrument_identity == FUTURES_CONTINUOUS_IDENTITY
    assert ev.futures_close == 103.0
    assert ev.vix_close == 14.5
    assert ev.completeness == COMPLETENESS_COMPLETE
    assert ev.is_final is True
    assert set(ev.source_observation_ids) == {"OBS-a", "OBS-b", "OBS-c"}
    assert set(ev.certification_refs) == {"ref-a", "ref-b", "ref-c"}


def test_from_snapshot_never_carries_a_computed_field():
    """Structural guarantee: even though VixSnapshot HAS a
    change_percent slot, RealityMemoryEvent must never expose it --
    the Reality-only decision (PHASE_17J0 §4) is enforced by the model
    shape itself, not by the field happening to be unset upstream."""
    assert not hasattr(RealityMemoryEvent, "vix_change_percent")
    assert "change_percent" not in RealityMemoryEvent.__dataclass_fields__


def test_from_snapshot_handles_absent_instruments_honestly():
    snap = MarketRealitySnapshot(
        date="2026-08-01", spot=None, futures=None, vix=None,
        completeness=COMPLETENESS_EMPTY, is_final=True,
        certification_refs=(), built_at="2026-08-14T00:00:00+05:30",
    )
    ev = RealityMemoryEvent.from_snapshot(snap)
    assert ev.spot_close is None and ev.spot_source is None
    assert ev.futures_close is None and ev.futures_instrument_identity is None
    assert ev.vix_close is None
    assert ev.completeness == COMPLETENESS_EMPTY
    assert ev.source_observation_ids == ()


def test_to_dict_from_dict_roundtrip():
    snap = MarketRealitySnapshot(
        date="2026-08-01",
        spot=SpotSnapshot(open=1.0, high=2.0, low=0.5, close=1.5, volume=None,
                           source=SOURCE_LIVE, source_observation_ids=("OBS-a",)),
        futures=None, vix=None,
        completeness=COMPLETENESS_PARTIAL, is_final=False,
        certification_refs=("ref-a",), built_at="2026-08-14T00:00:00+05:30",
    )
    ev = RealityMemoryEvent.from_snapshot(snap)
    roundtripped = RealityMemoryEvent.from_dict(ev.to_dict())
    assert roundtripped == ev


# --- RealityMemoryCatalog: reads fresh, never from the stale persisted snapshot store ---
def test_catalog_get_returns_an_event_for_a_fully_populated_date():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        date = "2026-08-01"
        store.write(_daily_obs(SPOT_SYMBOL, "SPOT", date))
        store.write(_daily_obs(VIX_SYMBOL, "INDEX", date))
        store.write(_daily_obs(FUTURES_CONTINUOUS_IDENTITY, "FUTURE", date))

        catalog = RealityMemoryCatalog(historical_store=store)
        ev = catalog.get(date)
        assert ev.date == date
        assert ev.completeness == COMPLETENESS_COMPLETE
        assert ev.spot_close == 102.0
        assert ev.futures_instrument_identity == FUTURES_CONTINUOUS_IDENTITY
        assert ev.vix_close == 102.0


def test_catalog_get_returns_an_honest_empty_event_for_an_uncovered_date():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        catalog = RealityMemoryCatalog(historical_store=store)
        ev = catalog.get("1900-01-01")
        assert ev.completeness == COMPLETENESS_EMPTY
        assert ev.spot_close is None
        assert ev.futures_close is None
        assert ev.vix_close is None


def test_catalog_never_reads_a_stale_persisted_snapshot():
    """The real bug this phase's design corrects: a catalog built over
    HistoricalObservationStore directly must reflect data ingested
    AFTER any persisted MarketRealitySnapshot row would have been
    built -- proven here by writing historical rows and confirming the
    catalog sees them with no separate snapshot-store write step at all."""
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        date = "2026-08-01"
        store.write(_daily_obs(SPOT_SYMBOL, "SPOT", date))
        catalog = RealityMemoryCatalog(historical_store=store)
        assert catalog.get(date).completeness == COMPLETENESS_PARTIAL

        # Add futures AFTER the catalog object already exists -- no
        # snapshot store to go stale, no cache to invalidate.
        store.write(_daily_obs(FUTURES_CONTINUOUS_IDENTITY, "FUTURE", date))
        store.write(_daily_obs(VIX_SYMBOL, "INDEX", date))
        assert catalog.get(date).completeness == COMPLETENESS_COMPLETE


def test_catalog_range_returns_one_event_per_calendar_date_inclusive():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        catalog = RealityMemoryCatalog(historical_store=store)
        events = catalog.range("2026-08-01", "2026-08-03")
        assert [e.date for e in events] == ["2026-08-01", "2026-08-02", "2026-08-03"]


def test_catalog_range_is_empty_when_end_precedes_start():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        catalog = RealityMemoryCatalog(historical_store=store)
        assert catalog.range("2026-08-05", "2026-08-01") == []


def test_catalog_get_is_read_only_never_mutates_the_store():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        store.write(_daily_obs(SPOT_SYMBOL, "SPOT", "2026-08-01"))
        before = store.count()
        catalog = RealityMemoryCatalog(historical_store=store)
        catalog.get("2026-08-01")
        catalog.range("2026-08-01", "2026-08-01")
        after = store.count()
        assert before == after == 1
