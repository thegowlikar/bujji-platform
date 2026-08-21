"""Phase 17H.5 — Market Reality Snapshot: the bridge between Historical
Reality (17H.4) and Live Reality (17E/17I).

Uses small, isolated fixtures (StaticCertificationGate + tmp_path
stores) rather than the real 28-year dataset -- deterministic, fast,
and independent of whatever the real stores happen to contain when this
suite runs.
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
from bujji.market_reality_snapshot.builder import build_market_reality_snapshot
from bujji.market_reality_snapshot.models import (
    COMPLETENESS_COMPLETE,
    COMPLETENESS_EMPTY,
    COMPLETENESS_PARTIAL,
    MarketRealitySnapshot,
    SOURCE_HISTORICAL,
    SOURCE_LIVE,
)
from bujji.market_reality_snapshot.store import (
    ConflictingSnapshotError,
    MarketRealitySnapshotStore,
)

CERT = reality_taxonomy.CERTIFIED_AVAILABLE
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
SPOT_SYMBOL = "NSE:NIFTY50-INDEX"


def _gate():
    return StaticCertificationGate(CERT)


def _hist_spot(date: str, close: float, ingestion_run_id="RUN-test"):
    return build_historical_observation(
        instrument_identity=SPOT_SYMBOL, instrument_type=reality_taxonomy.INSTRUMENT_SPOT,
        resolution=moc_taxonomy.RESOLUTION_DAILY, timestamp=f"{date}T09:15:00+05:30",
        payload={"open": close - 5, "high": close + 5, "low": close - 10, "close": close, "volume": None},
        source="fyers_historical", access_method="direct_sdk_fyers_historical_rest",
        source_epoch=1000000, source_symbol=SPOT_SYMBOL, raw_artifact_ref="x.json",
        ingestion_run_id=ingestion_run_id, retrieved_at="2026-08-13T16:00:00+05:30",
        certification_status=CERT, certification_ref="hist_cert@ts",
    )


def _live_obs(date_time: str, instrument_type: str, instrument: str, payload: dict,
              identity_fields=None):
    return build_raw_observation(
        kind=reality_taxonomy.KIND_QUOTE, instrument=instrument, instrument_type=instrument_type,
        payload=payload, source="fyers", access_method="direct_sdk_fyers_broker_py",
        capture_timestamp=date_time, event_timestamp=None,
        certification_status=CERT, identity_fields=identity_fields or {},
    )


@pytest.fixture
def hist_store(tmp_path):
    return HistoricalObservationStore(tmp_path / "hist.db")


@pytest.fixture
def live_store(tmp_path):
    return RawObservationStore(tmp_path / "layer0", _gate(), session_id="test-snapshot")


NOW = datetime.datetime(2026, 8, 14, 10, 0, tzinfo=IST)  # "tomorrow" relative to test dates below.


# --- Historical-only date: partial, honest, no fabrication -----------------
def test_historical_only_date_is_partial_with_no_fabricated_futures_or_vix(hist_store, live_store):
    hist_store.write(_hist_spot("2005-06-14", close=2112.35))

    snap = build_market_reality_snapshot("2005-06-14", historical_store=hist_store,
                                          live_store=live_store, now=NOW)

    assert snap.spot is not None
    assert snap.spot.close == 2112.35
    assert snap.spot.source == SOURCE_HISTORICAL
    assert snap.futures is None   # Missing means missing -- never zero, never estimated.
    assert snap.vix is None
    assert snap.completeness == COMPLETENESS_PARTIAL
    assert snap.is_final is True


# --- Combined date: historical spot + live futures/vix ----------------------
def test_combined_date_merges_historical_spot_with_live_futures_and_vix(hist_store, live_store):
    date = "2026-08-13"
    hist_store.write(_hist_spot(date, close=24395.85))
    live_store.append(_live_obs(f"{date}T13:00:00+05:30", reality_taxonomy.INSTRUMENT_FUTURE,
                                 "NSE:NIFTY26AUGFUT", {"ltp": 24464.0, "volume": 900000, "oi": 12000000},
                                 identity_fields={"expiry": "2026-08-25"}),
                       now=f"{date}T13:00:00+05:30")
    live_store.append(_live_obs(f"{date}T13:00:00+05:30", reality_taxonomy.INSTRUMENT_INDEX,
                                 "NSE:INDIAVIX-INDEX", {"ltp": 11.39}),
                       now=f"{date}T13:00:00+05:30")

    snap = build_market_reality_snapshot(date, historical_store=hist_store,
                                          live_store=live_store, now=NOW)

    assert snap.spot.source == SOURCE_HISTORICAL
    assert snap.futures is not None
    assert snap.futures.source == SOURCE_LIVE
    assert snap.futures.close == 24464.0
    assert snap.futures.instrument == "NSE:NIFTY26AUGFUT"
    assert snap.futures.expiry_date == "2026-08-25"
    assert snap.vix is not None
    assert snap.vix.close == 11.39
    assert snap.completeness == COMPLETENESS_COMPLETE


def test_lineage_traces_back_to_real_observation_ids(hist_store, live_store):
    """The stated requirement: this is a derived view, must preserve
    lineage back to reality."""
    date = "2026-08-13"
    hist_obs = _hist_spot(date, close=24395.85)
    hist_store.write(hist_obs)
    live_obs = _live_obs(f"{date}T13:00:00+05:30", reality_taxonomy.INSTRUMENT_INDEX,
                          "NSE:INDIAVIX-INDEX", {"ltp": 11.39})
    live_store.append(live_obs, now=f"{date}T13:00:00+05:30")

    snap = build_market_reality_snapshot(date, historical_store=hist_store,
                                          live_store=live_store, now=NOW)

    assert hist_obs.observation_id in snap.source_observation_ids
    assert live_obs.observation_id in snap.source_observation_ids
    assert "hist_cert@ts" in snap.certification_refs


# --- Live-only spot aggregation: real min/max/first/last, never fabricated --
def test_live_only_spot_is_aggregated_honestly_from_real_ticks(hist_store, live_store):
    """No historical bar for this date -- spot must fall back to
    aggregating that day's real live ticks, not fail or fabricate."""
    date = "2026-08-13"
    for i, ltp in enumerate([100.0, 105.0, 98.0, 102.0]):
        live_store.append(
            _live_obs(f"{date}T{10+i:02d}:00:00+05:30", reality_taxonomy.INSTRUMENT_SPOT,
                      SPOT_SYMBOL, {"ltp": ltp}),
            now=f"{date}T{10+i:02d}:00:00+05:30",
        )

    snap = build_market_reality_snapshot(date, historical_store=hist_store,
                                          live_store=live_store, now=NOW)

    assert snap.spot.source == SOURCE_LIVE
    assert snap.spot.open == 100.0    # First real tick.
    assert snap.spot.high == 105.0    # Real max.
    assert snap.spot.low == 98.0      # Real min.
    assert snap.spot.close == 102.0   # Last real tick.
    assert snap.spot.volume is None   # Live spot payload never carries volume -- never fabricated.


def test_historical_bar_takes_precedence_over_live_ticks_for_the_same_date(hist_store, live_store):
    """If both exist for the same date, the certified historical daily
    bar wins -- it is the authoritative EOD record, not an average or a
    merge of the two."""
    date = "2005-06-14"
    hist_store.write(_hist_spot(date, close=2112.35))
    live_store.append(_live_obs(f"{date}T10:00:00+05:30", reality_taxonomy.INSTRUMENT_SPOT,
                                 SPOT_SYMBOL, {"ltp": 9999.0}), now=f"{date}T10:00:00+05:30")

    snap = build_market_reality_snapshot(date, historical_store=hist_store,
                                          live_store=live_store, now=NOW)
    assert snap.spot.close == 2112.35
    assert snap.spot.source == SOURCE_HISTORICAL


# --- Fully empty date -- never raises -----------------------------------------
def test_date_with_nothing_at_all_is_empty_not_an_error(hist_store, live_store):
    snap = build_market_reality_snapshot("1990-01-07", historical_store=hist_store,
                                          live_store=live_store, now=NOW)
    assert snap.spot is None
    assert snap.futures is None
    assert snap.vix is None
    assert snap.completeness == COMPLETENESS_EMPTY
    assert snap.source_observation_ids == ()


# --- Round-trip serialization -------------------------------------------------
def test_snapshot_round_trips_through_dict(hist_store, live_store):
    hist_store.write(_hist_spot("2005-06-14", close=2112.35))
    snap = build_market_reality_snapshot("2005-06-14", historical_store=hist_store,
                                          live_store=live_store, now=NOW)
    restored = MarketRealitySnapshot.from_dict(snap.to_dict())
    assert restored.spot.close == snap.spot.close
    assert restored.futures is None
    assert restored.completeness == snap.completeness


# --- Store: settled-day conflict discipline ------------------------------------
def test_settled_day_write_is_idempotent_for_identical_content(hist_store, live_store, tmp_path):
    hist_store.write(_hist_spot("2005-06-14", close=2112.35))
    snap = build_market_reality_snapshot("2005-06-14", historical_store=hist_store,
                                          live_store=live_store, now=NOW)
    snap_store = MarketRealitySnapshotStore(tmp_path / "snap.db")

    assert snap_store.write(snap) is True
    assert snap_store.write(snap) is False  # Idempotent no-op.
    assert snap_store.count() == 1


def test_settled_day_conflict_is_raised_never_silently_overwritten(hist_store, live_store, tmp_path):
    snap_store = MarketRealitySnapshotStore(tmp_path / "snap.db")
    hist_store.write(_hist_spot("2005-06-14", close=2112.35))
    snap_v1 = build_market_reality_snapshot("2005-06-14", historical_store=hist_store,
                                             live_store=live_store, now=NOW)
    snap_store.write(snap_v1)

    # A different underlying fact for the same settled date (e.g. a
    # hypothetical late correction) -- must be a real, surfaced conflict.
    snap_v2 = MarketRealitySnapshot(
        date="2005-06-14", spot=None, futures=None, vix=None,
        completeness=COMPLETENESS_EMPTY, is_final=True, certification_refs=(),
        built_at="2026-08-14T10:00:00+05:30",
    )
    with pytest.raises(ConflictingSnapshotError):
        snap_store.write(snap_v2)
    assert snap_store.get("2005-06-14").spot.close == 2112.35  # Original untouched.


def test_in_progress_day_always_upserts_without_conflict(hist_store, live_store, tmp_path):
    """A live/in-progress day legitimately changes as more ticks arrive
    -- this must never raise, even though the content differs between
    writes."""
    snap_store = MarketRealitySnapshotStore(tmp_path / "snap.db")
    date = "2026-08-14"  # "Today" relative to NOW -- genuinely in-progress.

    live_store.append(_live_obs(f"{date}T09:20:00+05:30", reality_taxonomy.INSTRUMENT_INDEX,
                                 "NSE:INDIAVIX-INDEX", {"ltp": 11.0}), now=f"{date}T09:20:00+05:30")
    snap_v1 = build_market_reality_snapshot(date, historical_store=hist_store,
                                             live_store=live_store, now=NOW)
    assert snap_v1.is_final is False
    snap_store.write(snap_v1)

    live_store.append(_live_obs(f"{date}T09:21:00+05:30", reality_taxonomy.INSTRUMENT_INDEX,
                                 "NSE:INDIAVIX-INDEX", {"ltp": 11.5}), now=f"{date}T09:21:00+05:30")
    snap_v2 = build_market_reality_snapshot(date, historical_store=hist_store,
                                             live_store=live_store, now=NOW)
    assert snap_v2.vix.close != snap_v1.vix.close  # Genuinely different -- more ticks arrived.
    assert snap_store.write(snap_v2) is True  # No conflict raised.
    assert snap_store.get(date).vix.close == 11.5


def test_snapshot_store_restart_survival(hist_store, live_store, tmp_path):
    path = tmp_path / "snap.db"
    hist_store.write(_hist_spot("2005-06-14", close=2112.35))
    snap = build_market_reality_snapshot("2005-06-14", historical_store=hist_store,
                                          live_store=live_store, now=NOW)
    store1 = MarketRealitySnapshotStore(path)
    store1.write(snap)

    store2 = MarketRealitySnapshotStore(path)  # Real process-restart simulation.
    assert store2.count() == 1
    assert store2.get("2005-06-14").spot.close == 2112.35
    with pytest.raises(ConflictingSnapshotError):
        store2.write(MarketRealitySnapshot(
            date="2005-06-14", spot=None, futures=None, vix=None,
            completeness=COMPLETENESS_EMPTY, is_final=True, certification_refs=(),
            built_at="2026-08-14T10:00:00+05:30",
        ))


def test_deterministic_reconstruction_is_stable_for_a_settled_day(hist_store, live_store):
    """Rebuilding a settled day's snapshot twice must produce identical
    results -- no wall-clock dependency, no hidden randomness."""
    hist_store.write(_hist_spot("2005-06-14", close=2112.35))
    snap_a = build_market_reality_snapshot("2005-06-14", historical_store=hist_store,
                                            live_store=live_store, now=NOW)
    snap_b = build_market_reality_snapshot("2005-06-14", historical_store=hist_store,
                                            live_store=live_store, now=NOW)
    assert snap_a.to_dict() == snap_b.to_dict()  # Same `now` injected -> identical built_at too.
