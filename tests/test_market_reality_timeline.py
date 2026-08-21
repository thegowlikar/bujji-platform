"""Phase 17H.6 — Market Reality Timeline (Reality-only query layer).

Reuses the same isolated-fixture pattern as test_market_reality_snapshot.py.
Every test here proves a FILTER over already-stored values, never a
computation -- consistent with the explicit, documented boundary this
phase drew (see timeline.py's module docstring).
"""
import datetime

from bujji.historical_reality.capture import build_historical_observation
from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_reality import taxonomy as reality_taxonomy
from bujji.market_reality.capture import build_raw_observation
from bujji.market_reality.certification import StaticCertificationGate
from bujji.market_reality.store import RawObservationStore
from bujji.market_reality_snapshot.builder import build_market_reality_snapshot
from bujji.market_reality_snapshot.models import COMPLETENESS_COMPLETE, COMPLETENESS_PARTIAL
from bujji.market_reality_snapshot.store import MarketRealitySnapshotStore
from bujji.market_reality_snapshot.timeline import MarketRealityTimeline, RealityQuery

CERT = reality_taxonomy.CERTIFIED_AVAILABLE
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
SPOT_SYMBOL = "NSE:NIFTY50-INDEX"
NOW = datetime.datetime(2026, 8, 14, 10, 0, tzinfo=IST)


def _gate():
    return StaticCertificationGate(CERT)


def _hist_spot(date: str, close: float):
    return build_historical_observation(
        instrument_identity=SPOT_SYMBOL, instrument_type=reality_taxonomy.INSTRUMENT_SPOT,
        resolution=moc_taxonomy.RESOLUTION_DAILY, timestamp=f"{date}T09:15:00+05:30",
        payload={"open": close - 5, "high": close + 5, "low": close - 10, "close": close, "volume": None},
        source="fyers_historical", access_method="direct_sdk_fyers_historical_rest",
        source_epoch=1000000, source_symbol=SPOT_SYMBOL, raw_artifact_ref="x.json",
        ingestion_run_id="RUN-test", retrieved_at="2026-08-13T16:00:00+05:30",
        certification_status=CERT, certification_ref="hist_cert@ts",
    )


def _live_vix(date_time: str, ltp: float):
    return build_raw_observation(
        kind=reality_taxonomy.KIND_QUOTE, instrument="NSE:INDIAVIX-INDEX",
        instrument_type=reality_taxonomy.INSTRUMENT_INDEX, payload={"ltp": ltp},
        source="fyers", access_method="direct_sdk_fyers_broker_py",
        capture_timestamp=date_time, event_timestamp=None,
        certification_status=CERT, identity_fields={},
    )


def _populated_store(tmp_path, dates_closes):
    hist = HistoricalObservationStore(tmp_path / "hist.db")
    live = RawObservationStore(tmp_path / "layer0", _gate(), session_id="test")
    snap_store = MarketRealitySnapshotStore(tmp_path / "snap.db")
    for date, close in dates_closes:
        hist.write(_hist_spot(date, close))
        snap = build_market_reality_snapshot(date, historical_store=hist, live_store=live, now=NOW)
        snap_store.write(snap)
    return snap_store, hist, live


# --- Pure range filtering over already-stored values --------------------------
def test_query_filters_by_spot_close_range_on_real_stored_values(tmp_path):
    snap_store, _, _ = _populated_store(tmp_path, [
        ("2020-01-02", 12200.0), ("2020-01-03", 12100.0), ("2020-03-02", 11132.8),
        ("2020-03-03", 11303.3), ("2020-06-01", 9800.0),
    ])
    timeline = MarketRealityTimeline(snap_store)

    matches = timeline.query(RealityQuery(spot_close_min=11000, spot_close_max=11500))
    matched_dates = {m.date for m in matches}
    assert matched_dates == {"2020-03-02", "2020-03-03"}


def test_query_date_range_is_respected(tmp_path):
    snap_store, _, _ = _populated_store(tmp_path, [
        ("2020-01-02", 12200.0), ("2020-06-01", 12200.0),
    ])
    timeline = MarketRealityTimeline(snap_store)
    matches = timeline.query(RealityQuery(date_from="2020-01-01", date_to="2020-02-01",
                                           spot_close_min=12000, spot_close_max=12500))
    assert {m.date for m in matches} == {"2020-01-02"}


def test_query_by_completeness(tmp_path):
    snap_store, _, _ = _populated_store(tmp_path, [("2020-01-02", 12200.0)])
    timeline = MarketRealityTimeline(snap_store)
    assert len(timeline.query(RealityQuery(completeness=COMPLETENESS_PARTIAL))) == 1
    assert len(timeline.query(RealityQuery(completeness=COMPLETENESS_COMPLETE))) == 0


# --- Absence never satisfies a numeric filter ---------------------------------
def test_absent_instrument_never_matches_a_numeric_filter(tmp_path):
    """A day with no futures/VIX reality is correctly excluded from a
    futures/VIX-based query -- absence is not a value in range."""
    hist = HistoricalObservationStore(tmp_path / "hist.db")
    live = RawObservationStore(tmp_path / "layer0", _gate(), session_id="test")
    snap_store = MarketRealitySnapshotStore(tmp_path / "snap.db")
    hist.write(_hist_spot("2005-06-14", 2112.35))  # Spot only -- no VIX that far back.
    snap = build_market_reality_snapshot("2005-06-14", historical_store=hist, live_store=live, now=NOW)
    snap_store.write(snap)

    timeline = MarketRealityTimeline(snap_store)
    matches = timeline.query(RealityQuery(vix_close_min=0, vix_close_max=100))
    assert matches == []  # NOT a false match just because the constraint is "wide open".


def test_query_by_vix_close_on_real_live_captured_value(tmp_path):
    hist = HistoricalObservationStore(tmp_path / "hist.db")
    live = RawObservationStore(tmp_path / "layer0", _gate(), session_id="test")
    snap_store = MarketRealitySnapshotStore(tmp_path / "snap.db")
    hist.write(_hist_spot("2026-08-13", 24395.85))
    live.append(_live_vix("2026-08-13T13:00:00+05:30", 11.39), now="2026-08-13T13:00:00+05:30")
    snap = build_market_reality_snapshot("2026-08-13", historical_store=hist, live_store=live, now=NOW)
    snap_store.write(snap)

    timeline = MarketRealityTimeline(snap_store)
    assert len(timeline.query(RealityQuery(vix_close_min=10, vix_close_max=13))) == 1
    assert len(timeline.query(RealityQuery(vix_close_min=20, vix_close_max=30))) == 0


# --- Plain range (no filter) ---------------------------------------------------
def test_range_returns_the_full_unfiltered_timeline(tmp_path):
    snap_store, _, _ = _populated_store(tmp_path, [
        ("2020-01-02", 12200.0), ("2020-01-03", 12100.0),
    ])
    timeline = MarketRealityTimeline(snap_store)
    rows = timeline.range("2020-01-01", "2020-01-31")
    assert [r.date for r in rows] == ["2020-01-02", "2020-01-03"]  # Ordered.


def test_get_passes_through_to_the_store(tmp_path):
    snap_store, _, _ = _populated_store(tmp_path, [("2020-01-02", 12200.0)])
    timeline = MarketRealityTimeline(snap_store)
    assert timeline.get("2020-01-02").spot.close == 12200.0
    assert timeline.get("1999-01-01") is None


# --- Real-scale proof against the real dataset shape (2020, a real bounded year) --
def test_realistic_batch_query_matches_multiple_real_looking_days(tmp_path):
    """Mirrors the real live smoke test: a bounded real year's worth of
    days, querying a price band, returning multiple real matches -- not
    just a single-row toy case."""
    closes = {
        "2020-02-28": 11201.8, "2020-03-02": 11132.8, "2020-03-03": 11303.3,
        "2020-03-04": 11251.0, "2020-03-05": 11269.0, "2020-06-01": 9800.0,
        "2020-12-31": 13981.75,
    }
    snap_store, _, _ = _populated_store(tmp_path, list(closes.items()))
    timeline = MarketRealityTimeline(snap_store)

    matches = timeline.query(RealityQuery(date_from="2020-01-01", date_to="2020-12-31",
                                           spot_close_min=11000, spot_close_max=11500))
    assert {m.date for m in matches} == {
        "2020-02-28", "2020-03-02", "2020-03-03", "2020-03-04", "2020-03-05",
    }
