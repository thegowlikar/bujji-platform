"""Phase 18.3 -- MarketRealitySnapshot Identity Hardening tests:
component-level observed_at, deterministic fingerprint(), and
reconstruction_version."""
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import dataclasses

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
    RECONSTRUCTION_VERSION,
    RESOLUTION_FIVE_MINUTE,
    SpotSnapshot,
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


# --- observed_at, per component ---------------------------------------------
def test_daily_historical_spot_observed_at_is_the_bars_own_timestamp(store):
    store.write(_obs(SPOT_SYMBOL, "SPOT", moc_taxonomy.RESOLUTION_DAILY, "2026-08-13T09:15:00+05:30",
                      {"open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0}))
    snap = build_market_reality_snapshot("2026-08-13", historical_store=store)
    assert snap.spot.observed_at == "2026-08-13T09:15:00+05:30"


def test_intraday_spot_observed_at_matches_the_picked_row(store):
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:15:00+05:30",
                      {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}))
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:20:00+05:30",
                      {"open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5}))
    snap = build_market_reality_snapshot("2026-08-14", historical_store=store,
                                          resolution=RESOLUTION_FIVE_MINUTE, as_of_time="2026-08-14T09:22:00+05:30")
    assert snap.spot.observed_at == "2026-08-14T09:20:00+05:30"
    assert snap.spot.observed_at != snap.as_of  # the whole point of Gap 1: they can legitimately differ.


def test_option_contracts_expose_independent_observed_at(store):
    """The exact Gap 1 worked example: one contract's own last real row
    can be earlier than another's, and both can differ from the
    requested as_of_time -- each OptionContractSnapshot must carry its
    OWN observed_at, not the snapshot's."""
    ce = _option_identity("2026-08-18", 21800, "CE")
    late_ce = _option_identity("2026-08-18", 22200, "CE")
    store.write(_obs(ce, "OPTION", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:34:58+05:30",
                      {"ltp": 250.0}, value_kind=moc_taxonomy.VALUE_KIND_MAPPING))
    store.write(_obs(late_ce, "OPTION", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:35:00+05:30",
                      {"ltp": 5.0}, value_kind=moc_taxonomy.VALUE_KIND_MAPPING))

    snap = build_market_reality_snapshot("2026-08-14", historical_store=store,
                                          resolution=RESOLUTION_FIVE_MINUTE, as_of_time="2026-08-14T09:35:00+05:30")
    by_identity = {c.identity: c for c in snap.options.contracts}
    assert by_identity[ce].observed_at == "2026-08-14T09:34:58+05:30"
    assert by_identity[late_ce].observed_at == "2026-08-14T09:35:00+05:30"
    assert by_identity[ce].observed_at != by_identity[late_ce].observed_at


def test_observed_at_none_when_component_absent(store):
    snap = build_market_reality_snapshot("2026-08-14", historical_store=store,
                                          resolution=RESOLUTION_FIVE_MINUTE, as_of_time="2026-08-14T09:35:00+05:30")
    assert snap.spot is None  # nothing written -- no fabricated observed_at either.


# --- fingerprint() determinism -----------------------------------------------
def test_fingerprint_identical_across_repeated_builds(store):
    store.write(_obs(SPOT_SYMBOL, "SPOT", moc_taxonomy.RESOLUTION_DAILY, "2026-08-13T09:15:00+05:30",
                      {"open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0}))
    snap1 = build_market_reality_snapshot("2026-08-13", historical_store=store)
    snap2 = build_market_reality_snapshot("2026-08-13", historical_store=store)
    assert snap1.fingerprint() == snap2.fingerprint()


def test_fingerprint_differs_for_genuinely_different_content(store):
    store.write(_obs(SPOT_SYMBOL, "SPOT", moc_taxonomy.RESOLUTION_DAILY, "2026-08-13T09:15:00+05:30",
                      {"open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0}))
    store.write(_obs("NSE:INDIAVIX-INDEX", "INDEX", moc_taxonomy.RESOLUTION_DAILY, "2026-08-13T09:15:00+05:30",
                      {"open": 12.0, "high": 12.5, "low": 11.5, "close": 12.1}))
    snap_spot_only = build_market_reality_snapshot("2026-08-12", historical_store=store)  # no data at all -> EMPTY
    snap_with_data = build_market_reality_snapshot("2026-08-13", historical_store=store)
    assert snap_spot_only.fingerprint() != snap_with_data.fingerprint()


def test_fingerprint_excludes_reconstruction_version():
    """The core Gap 3 requirement: changing ONLY reconstruction_version
    (simulating a future logic-version bump with byte-identical
    underlying data) must NOT change the fingerprint -- that is what
    lets (fingerprint, reconstruction_version) be compared as a pair to
    tell 'data changed' apart from 'logic changed'."""
    snap = MarketRealitySnapshot(
        date="2026-08-13", spot=None, futures=None, vix=None,
        completeness="EMPTY", is_final=True, certification_refs=(),
        built_at="2026-08-13T09:15:00+05:30",
    )
    bumped = dataclasses.replace(snap, reconstruction_version="99.0.0")
    assert snap.fingerprint() == bumped.fingerprint()
    assert snap.reconstruction_version != bumped.reconstruction_version


def test_fingerprint_excludes_built_at_and_is_final():
    """Two builds of identical market content, differing only in wall-
    clock build time / is_final classification, must fingerprint
    identically -- neither is a market fact."""
    snap_a = MarketRealitySnapshot(
        date="2026-08-13", spot=None, futures=None, vix=None,
        completeness="EMPTY", is_final=True, certification_refs=(),
        built_at="2026-08-13T09:15:00+05:30",
    )
    snap_b = dataclasses.replace(snap_a, built_at="2027-01-01T00:00:00+05:30", is_final=False)
    assert snap_a.fingerprint() == snap_b.fingerprint()


def test_fingerprint_reuses_replay_engine_fingerprint_state_not_a_new_hash():
    from bujji.replay_engine.engine import fingerprint_state
    snap = MarketRealitySnapshot(
        date="2026-08-13", spot=None, futures=None, vix=None,
        completeness="EMPTY", is_final=True, certification_refs=(),
        built_at="2026-08-13T09:15:00+05:30",
    )
    assert snap.fingerprint() == fingerprint_state(snap._fingerprint_payload())


def test_fingerprint_is_a_real_sha256_hex_digest():
    snap = MarketRealitySnapshot(
        date="2026-08-13", spot=None, futures=None, vix=None,
        completeness="EMPTY", is_final=True, certification_refs=(),
        built_at="2026-08-13T09:15:00+05:30",
    )
    fp = snap.fingerprint()
    assert len(fp) == 64
    int(fp, 16)  # raises if not valid hex


# --- reconstruction_version ---------------------------------------------------
def test_reconstruction_version_stamped_on_every_build(store):
    snap = build_market_reality_snapshot("2026-08-13", historical_store=store)
    assert snap.reconstruction_version == RECONSTRUCTION_VERSION
    assert snap.reconstruction_version == "18.3.0"


# --- Backward compatibility: pre-18.3 (and pre-18.1) records ------------------
def test_pre_18_3_record_defaults_new_fields_correctly():
    """Simulates a real 1.1.0-era snapshot (post-18.1, pre-18.3) --
    has options/resolution/as_of but no observed_at/reconstruction_version."""
    legacy_dict = {
        "date": "2026-08-14", "spot": None, "futures": None, "vix": None,
        "completeness": "EMPTY", "is_final": True, "certification_refs": [],
        "built_at": "2026-08-14T09:15:00+05:30", "schema_version": "1.1.0",
        "options": None, "resolution": "DAILY", "as_of": None,
    }
    snap = MarketRealitySnapshot.from_dict(legacy_dict)
    assert snap.reconstruction_version == RECONSTRUCTION_VERSION
    # fingerprint() must not raise on a legacy-shaped, from_dict-built object.
    fp = snap.fingerprint()
    assert len(fp) == 64


def test_pre_18_1_1_0_0_record_still_round_trips_through_18_3_code():
    """The original 252-row shape -- none of options/resolution/as_of/
    observed_at/reconstruction_version present at all."""
    legacy_dict = {
        "date": "2020-01-01", "spot": None, "futures": None, "vix": None,
        "completeness": "EMPTY", "is_final": True, "certification_refs": [],
        "built_at": "2020-01-01T09:15:00+05:30", "schema_version": "1.0.0",
    }
    snap = MarketRealitySnapshot.from_dict(legacy_dict)
    assert snap.reconstruction_version == RECONSTRUCTION_VERSION
    assert snap.options is None
    assert snap.resolution == "DAILY"


def test_component_snapshot_serialization_round_trips_observed_at():
    s = SpotSnapshot(open=1.0, high=2.0, low=0.5, close=1.5, volume=100.0,
                      source="historical", source_observation_ids=("OBS-x",),
                      observed_at="2026-08-13T09:15:00+05:30")
    rebuilt = SpotSnapshot.from_dict(s.to_dict())
    assert rebuilt == s
    assert rebuilt.observed_at == "2026-08-13T09:15:00+05:30"


def test_component_snapshot_from_dict_defaults_observed_at_none_when_absent():
    legacy = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100.0,
              "source": "historical", "source_observation_ids": ["OBS-x"]}
    rebuilt = SpotSnapshot.from_dict(legacy)
    assert rebuilt.observed_at is None


# --- No-look-ahead preservation (re-proven under the hardened model) --------
def test_no_look_ahead_still_holds_with_observed_at_present(store):
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:15:00+05:30",
                      {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}))
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, "2026-08-14T15:25:00+05:30",
                      {"open": 200.0, "high": 210.0, "low": 190.0, "close": 205.0}))
    snap = build_market_reality_snapshot("2026-08-14", historical_store=store,
                                          resolution=RESOLUTION_FIVE_MINUTE, as_of_time="2026-08-14T09:15:00+05:30")
    assert snap.spot.observed_at == "2026-08-14T09:15:00+05:30"
    assert snap.spot.observed_at != "2026-08-14T15:25:00+05:30"
    assert snap.spot.close == 100.5


# --- Existing test files must still pass unmodified (re-affirmed here) ------
def test_existing_daily_snapshot_test_module_still_importable():
    import tests.test_market_reality_snapshot  # noqa: F401
    import tests.test_market_reality_reconstruction  # noqa: F401
