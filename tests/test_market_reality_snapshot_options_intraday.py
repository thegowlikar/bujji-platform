"""Phase 18.1 -- options + 5-minute point-in-time extension to
market_reality_snapshot. Uses the real HistoricalObservationStore
(temp file per test) and the real build_historical_observation() path,
same discipline as tests/test_options_reality_capture.py."""
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import pytest

from bujji.historical_reality.capture import build_historical_observation
from bujji.historical_reality.store import HistoricalObservationStore
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_reality_snapshot.builder import (
    OPTIONS_UNDERLYING,
    build_market_reality_snapshot,
)
from bujji.market_reality_snapshot.models import (
    MarketRealitySnapshot,
    OptionContractSnapshot,
    OptionsSnapshot,
    RESOLUTION_DAILY,
    RESOLUTION_FIVE_MINUTE,
    SOURCE_HISTORICAL,
)

SPOT_SYMBOL = "NSE:NIFTY50-INDEX"


def _obs(identity, instrument_type, resolution, timestamp, payload, value_kind=moc_taxonomy.VALUE_KIND_OHLC):
    return build_historical_observation(
        instrument_identity=identity, instrument_type=instrument_type,
        resolution=resolution, timestamp=timestamp, payload=payload,
        source="fyers", access_method="test_access_method",
        source_epoch=1755000000, source_symbol=identity,
        raw_artifact_ref="", ingestion_run_id="RUN-test", retrieved_at=timestamp,
        certification_status="CERTIFIED_AVAILABLE", certification_ref="ref-1",
        value_kind=value_kind,
    )


def _option_identity(expiry, strike, option_type):
    return f"{OPTIONS_UNDERLYING}|{expiry}|{strike}|{option_type}"


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as d:
        yield HistoricalObservationStore(str(Path(d) / "hist.db"))


# --- Backward compatibility: DAILY path is untouched -----------------------
def test_daily_default_resolution_unchanged():
    """No resolution/as_of_time kwargs at all -- must behave exactly as
    every pre-18.1 caller already relies on."""
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        store.write(_obs(SPOT_SYMBOL, "SPOT", moc_taxonomy.RESOLUTION_DAILY,
                          "2026-08-13T09:15:00+05:30",
                          {"open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0}))
        snap = build_market_reality_snapshot("2026-08-13", historical_store=store)
        assert snap.resolution == RESOLUTION_DAILY
        assert snap.as_of is None
        assert snap.spot.close == 105.0
        assert snap.options is None


def test_daily_snapshot_serialization_round_trips_with_new_fields():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        store.write(_obs(SPOT_SYMBOL, "SPOT", moc_taxonomy.RESOLUTION_DAILY,
                          "2026-08-13T09:15:00+05:30",
                          {"open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0}))
        snap = build_market_reality_snapshot("2026-08-13", historical_store=store)
        rebuilt = MarketRealitySnapshot.from_dict(snap.to_dict())
        assert rebuilt == snap


def test_from_dict_on_a_pre_18_1_record_defaults_new_fields_correctly():
    """Simulates one of the 252 real, already-persisted 1.0.0 rows --
    no `options`/`resolution`/`as_of` keys at all."""
    legacy_dict = {
        "date": "2020-01-01", "spot": None, "futures": None, "vix": None,
        "completeness": "EMPTY", "is_final": True, "certification_refs": [],
        "built_at": "2020-01-01T09:15:00+05:30", "schema_version": "1.0.0",
    }
    snap = MarketRealitySnapshot.from_dict(legacy_dict)
    assert snap.options is None
    assert snap.resolution == RESOLUTION_DAILY
    assert snap.as_of is None


# --- New: resolution/as_of_time validation ----------------------------------
def test_unknown_resolution_raises(store):
    with pytest.raises(ValueError):
        build_market_reality_snapshot("2026-08-14", historical_store=store, resolution="WEEKLY")


def test_five_minute_without_as_of_time_raises(store):
    with pytest.raises(ValueError):
        build_market_reality_snapshot("2026-08-14", historical_store=store, resolution=RESOLUTION_FIVE_MINUTE)


# --- New: point-in-time reconstruction, spot/futures/vix -------------------
def test_intraday_picks_latest_row_at_or_before_as_of_time(store):
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:15:00+05:30",
                      {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}))
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:20:00+05:30",
                      {"open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5}))
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:25:00+05:30",
                      {"open": 101.5, "high": 103.0, "low": 101.0, "close": 102.5}))

    snap = build_market_reality_snapshot(
        "2026-08-14", historical_store=store,
        resolution=RESOLUTION_FIVE_MINUTE, as_of_time="2026-08-14T09:20:00+05:30",
    )
    assert snap.spot.close == 101.5  # the 09:20 bar, not 09:25's (which is after as_of_time).
    assert snap.as_of == "2026-08-14T09:20:00+05:30"


def test_intraday_no_look_ahead_a_later_bar_never_leaks_in(store):
    """The direct proof this phase's own point-in-time correctness
    requirement demands: a bar timestamped AFTER as_of_time must never
    appear in the result, under any circumstance."""
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:15:00+05:30",
                      {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}))
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, "2026-08-14T15:25:00+05:30",
                      {"open": 200.0, "high": 210.0, "low": 190.0, "close": 205.0}))

    snap = build_market_reality_snapshot(
        "2026-08-14", historical_store=store,
        resolution=RESOLUTION_FIVE_MINUTE, as_of_time="2026-08-14T09:15:00+05:30",
    )
    assert snap.spot.close == 100.5
    assert snap.spot.close != 205.0
    assert snap.spot.source_observation_ids[0] != "205.0"  # sanity: not the late bar's value


def test_intraday_before_any_data_returns_none_not_stale_carry_forward(store):
    """No fabrication: a moment before the first real bar of the day
    must yield None, never the previous day's close carried forward."""
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:15:00+05:30",
                      {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}))
    snap = build_market_reality_snapshot(
        "2026-08-14", historical_store=store,
        resolution=RESOLUTION_FIVE_MINUTE, as_of_time="2026-08-14T09:10:00+05:30",
    )
    assert snap.spot is None
    assert snap.completeness == "EMPTY"


# --- New: options reconstruction --------------------------------------------
def test_options_reconstruction_returns_real_contracts(store):
    ce = _option_identity("2026-08-18", 21800, "CE")
    pe = _option_identity("2026-08-18", 21800, "PE")
    store.write(_obs(ce, "OPTION", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:15:00+05:30",
                      {"ltp": 250.5, "bid": 250.0, "ask": 251.0, "open_interest": 1000, "volume": 500},
                      value_kind=moc_taxonomy.VALUE_KIND_MAPPING))
    store.write(_obs(pe, "OPTION", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:15:00+05:30",
                      {"ltp": 180.0, "bid": 179.5, "ask": 180.5, "open_interest": 800, "volume": 400},
                      value_kind=moc_taxonomy.VALUE_KIND_MAPPING))

    snap = build_market_reality_snapshot(
        "2026-08-14", historical_store=store,
        resolution=RESOLUTION_FIVE_MINUTE, as_of_time="2026-08-14T09:15:00+05:30",
    )
    assert snap.options is not None
    assert len(snap.options.contracts) == 2
    identities = {c.identity for c in snap.options.contracts}
    assert identities == {ce, pe}
    ce_contract = next(c for c in snap.options.contracts if c.identity == ce)
    assert ce_contract.expiry == "2026-08-18"
    assert ce_contract.strike == 21800.0
    assert ce_contract.option_type == "CE"
    assert ce_contract.ltp == 250.5
    assert ce_contract.source == SOURCE_HISTORICAL


def test_options_reconstruction_picks_latest_per_contract_independently(store):
    """A real, live-observed scenario (PHASE_18_0 §5): different
    contracts can have different "most recent" rows -- e.g. a new
    strike listed partway through the day. Each contract's own latest
    row at-or-before as_of_time must be picked independently, not one
    shared cycle timestamp for all."""
    ce = _option_identity("2026-08-18", 21800, "CE")
    late_ce = _option_identity("2026-08-18", 22200, "CE")  # newly listed strike.
    store.write(_obs(ce, "OPTION", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:15:00+05:30",
                      {"ltp": 250.0, "bid": 249.5, "ask": 250.5, "open_interest": 900, "volume": 100},
                      value_kind=moc_taxonomy.VALUE_KIND_MAPPING))
    store.write(_obs(ce, "OPTION", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:20:00+05:30",
                      {"ltp": 255.0, "bid": 254.5, "ask": 255.5, "open_interest": 950, "volume": 150},
                      value_kind=moc_taxonomy.VALUE_KIND_MAPPING))
    store.write(_obs(late_ce, "OPTION", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:20:00+05:30",
                      {"ltp": 5.0, "bid": 4.5, "ask": 5.5, "open_interest": 10, "volume": 5},
                      value_kind=moc_taxonomy.VALUE_KIND_MAPPING))

    snap = build_market_reality_snapshot(
        "2026-08-14", historical_store=store,
        resolution=RESOLUTION_FIVE_MINUTE, as_of_time="2026-08-14T09:20:00+05:30",
    )
    assert len(snap.options.contracts) == 2
    ce_contract = next(c for c in snap.options.contracts if c.identity == ce)
    assert ce_contract.ltp == 255.0  # the 09:20 row, not the stale 09:15 one.


def test_options_no_look_ahead(store):
    ce = _option_identity("2026-08-18", 21800, "CE")
    store.write(_obs(ce, "OPTION", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:15:00+05:30",
                      {"ltp": 250.0, "bid": 249.5, "ask": 250.5, "open_interest": 900, "volume": 100},
                      value_kind=moc_taxonomy.VALUE_KIND_MAPPING))
    store.write(_obs(ce, "OPTION", RESOLUTION_FIVE_MINUTE, "2026-08-14T15:25:00+05:30",
                      {"ltp": 999.0, "bid": 998.5, "ask": 999.5, "open_interest": 9999, "volume": 9999},
                      value_kind=moc_taxonomy.VALUE_KIND_MAPPING))

    snap = build_market_reality_snapshot(
        "2026-08-14", historical_store=store,
        resolution=RESOLUTION_FIVE_MINUTE, as_of_time="2026-08-14T09:15:00+05:30",
    )
    assert snap.options.contracts[0].ltp == 250.0


def test_daily_snapshot_never_surfaces_five_minute_option_data():
    """Options have never been ingested at DAILY resolution
    (PHASE_18_0 §6) -- the DAILY path must not accidentally pick up
    FIVE_MINUTE option rows through a resolution mismatch."""
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        ce = _option_identity("2026-08-18", 21800, "CE")
        store.write(_obs(ce, "OPTION", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:15:00+05:30",
                          {"ltp": 250.0, "bid": 249.5, "ask": 250.5, "open_interest": 900, "volume": 100},
                          value_kind=moc_taxonomy.VALUE_KIND_MAPPING))
        snap = build_market_reality_snapshot("2026-08-14", historical_store=store)
        assert snap.options is None


def test_options_snapshot_serialization_round_trips(store):
    ce = _option_identity("2026-08-18", 21800, "CE")
    store.write(_obs(ce, "OPTION", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:15:00+05:30",
                      {"ltp": 250.0, "bid": 249.5, "ask": 250.5, "open_interest": 900, "volume": 100},
                      value_kind=moc_taxonomy.VALUE_KIND_MAPPING))
    snap = build_market_reality_snapshot(
        "2026-08-14", historical_store=store,
        resolution=RESOLUTION_FIVE_MINUTE, as_of_time="2026-08-14T09:15:00+05:30",
    )
    rebuilt = MarketRealitySnapshot.from_dict(snap.to_dict())
    assert rebuilt == snap
    assert isinstance(rebuilt.options, OptionsSnapshot)
    assert isinstance(rebuilt.options.contracts[0], OptionContractSnapshot)


def test_range_by_prefix_is_read_only_and_additive():
    """The new store method must not disturb the existing exact-match
    range() query or the write path."""
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "hist.db"))
        ce = _option_identity("2026-08-18", 21800, "CE")
        other_underlying = "BANKNIFTY|2026-08-18|48000|CE"
        store.write(_obs(ce, "OPTION", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:15:00+05:30",
                          {"ltp": 250.0}, value_kind=moc_taxonomy.VALUE_KIND_MAPPING))
        store.write(_obs(other_underlying, "OPTION", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:15:00+05:30",
                          {"ltp": 500.0}, value_kind=moc_taxonomy.VALUE_KIND_MAPPING))

        nifty_rows = store.range_by_prefix("NIFTY|", RESOLUTION_FIVE_MINUTE,
                                            "2026-08-14T00:00:00+05:30", "2026-08-14T23:59:59+05:30")
        assert len(nifty_rows) == 1
        assert nifty_rows[0].instrument == ce

        exact_rows = store.range(ce, RESOLUTION_FIVE_MINUTE,
                                  "2026-08-14T00:00:00+05:30", "2026-08-14T23:59:59+05:30")
        assert len(exact_rows) == 1
        assert exact_rows[0].instrument == ce
