"""Phase 18.12 -- Dataset Artifact Registry tests: DatasetIdentity
determinism, DatasetArtifact immutability/persistence across a
simulated restart, verify_artifact()'s real detection power, and
regression proof that DatasetVersion/MarketRealitySnapshot/no-look-
ahead are all unchanged by this phase."""
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
from bujji.market_reality_snapshot.dataset_artifact import (
    ArtifactVerificationResult,
    DatasetArtifact,
    DatasetIdentity,
    create_artifact,
    derive_dataset_identity,
    get_artifact,
    list_artifacts,
    verify_artifact,
)
from bujji.market_reality_snapshot.dataset_artifact_store import (
    ConflictingDatasetArtifactError,
    DatasetArtifactStore,
)
from bujji.market_reality_snapshot.dataset_version import build_dataset_version
from bujji.market_reality_snapshot.models import RESOLUTION_FIVE_MINUTE

SPOT_SYMBOL = "NSE:NIFTY50-INDEX"
VIX_SYMBOL = "NSE:INDIAVIX-INDEX"
FUTURES_SYMBOL = "NIFTY_FUT_CONTINUOUS"


def _obs(identity, instrument_type, resolution, timestamp, payload, value_kind=moc_taxonomy.VALUE_KIND_OHLC,
         run_id="RUN-x", cert_ref="ref-1"):
    return build_historical_observation(
        instrument_identity=identity, instrument_type=instrument_type,
        resolution=resolution, timestamp=timestamp, payload=payload,
        source="fyers", access_method="test_access_method",
        source_epoch=1755000000, source_symbol=identity,
        raw_artifact_ref="", ingestion_run_id=run_id, retrieved_at=timestamp,
        certification_status="CERTIFIED_AVAILABLE", certification_ref=cert_ref,
        value_kind=value_kind,
    )


def _write_full_session(store, ts):
    store.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 1, "high": 2, "low": 0.5, "close": 1.5}, run_id="RUN-spot", cert_ref="cert-spot"))
    store.write(_obs(FUTURES_SYMBOL, "FUTURE", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 1, "high": 2, "low": 0.5, "close": 1.5}, run_id="RUN-fut", cert_ref="cert-fut"))
    store.write(_obs(VIX_SYMBOL, "INDEX", RESOLUTION_FIVE_MINUTE, ts,
                      {"open": 12, "high": 12.5, "low": 11.5, "close": 12.1}, run_id="RUN-vix", cert_ref="cert-vix"))
    store.write(_obs(f"{OPTIONS_UNDERLYING}|2026-08-18|21800|CE", "OPTION", RESOLUTION_FIVE_MINUTE, ts,
                      {"ltp": 250.0}, value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
                      run_id="RUN-opt", cert_ref="cert-opt"))


@pytest.fixture
def stores():
    with tempfile.TemporaryDirectory() as d:
        hist = HistoricalObservationStore(str(Path(d) / "hist.db"))
        art = DatasetArtifactStore(str(Path(d) / "artifacts.db"))
        yield hist, art


# --- DatasetIdentity: same content -> same identity ---------------------------
def test_dataset_identity_deterministic_across_two_independent_derivations(stores):
    hist, _ = stores
    _write_full_session(hist, "2026-08-14T09:15:00+05:30")
    dv1 = build_dataset_version("2026-08-14", "2026-08-14", historical_store=hist,
                                 resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:15:00+05:30")
    dv2 = build_dataset_version("2026-08-14", "2026-08-14", historical_store=hist,
                                 resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:15:00+05:30")
    id1 = derive_dataset_identity(dv1)
    id2 = derive_dataset_identity(dv2)
    assert id1.dataset_id == id2.dataset_id
    assert id1.fingerprint == id2.fingerprint


def test_dataset_identity_differs_for_different_schema_version(stores):
    hist, _ = stores
    _write_full_session(hist, "2026-08-14T09:15:00+05:30")
    dv = build_dataset_version("2026-08-14", "2026-08-14", historical_store=hist,
                                resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:15:00+05:30")
    id_a = derive_dataset_identity(dv, schema_version="1.2.0")
    id_b = derive_dataset_identity(dv, schema_version="9.9.9")
    assert id_a.dataset_id != id_b.dataset_id
    assert id_a.fingerprint == id_b.fingerprint  # fingerprint is schema-independent by design.


def test_identity_round_trips_through_dict():
    identity = DatasetIdentity(dataset_id="x", fingerprint="y", date_range=("2026-08-14", "2026-08-14"),
                                resolution="FIVE_MINUTE", instruments=("spot",),
                                reconstruction_version="18.3.0", schema_version="1.2.0")
    assert DatasetIdentity.from_dict(identity.to_dict()) == identity


# --- DatasetArtifact creation + persistence + immutability --------------------
def test_create_artifact_persists_real_fields(stores):
    hist, art = stores
    _write_full_session(hist, "2026-08-14T09:15:00+05:30")
    artifact = create_artifact("2026-08-14", "2026-08-14", historical_store=hist, artifact_store=art,
                                resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:15:00+05:30",
                                created_by="test")
    assert artifact.artifact_id.startswith("ART-")
    assert len(artifact.dataset_id) == 64
    assert len(artifact.fingerprint) == 64
    assert artifact.reconstruction_version == "18.3.0"
    assert artifact.code_identity["resolved"] is True  # real git repo -- resolved, not guessed.
    assert set(artifact.certification_references) == {"cert-spot", "cert-fut", "cert-vix", "cert-opt"}
    assert set(artifact.ingestion_run_references) == {"RUN-spot", "RUN-fut", "RUN-vix", "RUN-opt"}
    assert art.count() == 1


def test_artifact_id_never_regenerated_on_identical_recreation(stores):
    """Two separate create_artifact() calls for the same content
    produce the SAME dataset_id (content identity) but DIFFERENT
    artifact_id (event identity) -- PHASE_18_11's own worked
    distinction, proven live."""
    hist, art = stores
    _write_full_session(hist, "2026-08-14T09:15:00+05:30")
    a1 = create_artifact("2026-08-14", "2026-08-14", historical_store=hist, artifact_store=art,
                          resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:15:00+05:30")
    a2 = create_artifact("2026-08-14", "2026-08-14", historical_store=hist, artifact_store=art,
                          resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:15:00+05:30")
    assert a1.dataset_id == a2.dataset_id
    assert a1.artifact_id != a2.artifact_id
    assert art.count() == 2


def test_store_refuses_to_silently_overwrite_a_conflicting_record(stores):
    _, art = stores
    art.write("ART-fixed", "ds-1", "2026-08-14T09:00:00+05:30", {"v": 1})
    assert art.write("ART-fixed", "ds-1", "2026-08-14T09:00:00+05:30", {"v": 1}) is False  # idempotent no-op.
    with pytest.raises(ConflictingDatasetArtifactError):
        art.write("ART-fixed", "ds-1", "2026-08-14T09:00:00+05:30", {"v": 2})  # different content, same id.


def test_artifact_survives_simulated_process_restart(stores):
    """Fresh DatasetArtifactStore/HistoricalObservationStore objects
    against the SAME files -- the real proxy for a process restart a
    unit test can exercise."""
    hist, art = stores
    _write_full_session(hist, "2026-08-14T09:15:00+05:30")
    created = create_artifact("2026-08-14", "2026-08-14", historical_store=hist, artifact_store=art,
                               resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:15:00+05:30")

    reopened_art = DatasetArtifactStore(art.path)
    reopened_hist = HistoricalObservationStore(hist.path)
    reloaded = get_artifact(created.artifact_id, artifact_store=reopened_art)
    assert reloaded is not None
    assert reloaded == created  # frozen dataclass equality -- every field survived the round trip.

    result = verify_artifact(created.artifact_id, artifact_store=reopened_art, historical_store=reopened_hist)
    assert result.is_valid
    assert result.rebuilt_fingerprint == created.fingerprint


def test_list_artifacts_filters_by_dataset_id(stores):
    hist, art = stores
    _write_full_session(hist, "2026-08-14T09:15:00+05:30")
    a1 = create_artifact("2026-08-14", "2026-08-14", historical_store=hist, artifact_store=art,
                          resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:15:00+05:30")
    all_artifacts = list_artifacts(artifact_store=art)
    assert len(all_artifacts) == 1
    same_dataset = list_artifacts(artifact_store=art, dataset_id=a1.dataset_id)
    assert len(same_dataset) == 1
    other_dataset = list_artifacts(artifact_store=art, dataset_id="nonexistent")
    assert other_dataset == ()


# --- verify_artifact(): real detection, not always-true -----------------------
def test_verify_artifact_not_found():
    with tempfile.TemporaryDirectory() as d:
        hist = HistoricalObservationStore(str(Path(d) / "hist.db"))
        art = DatasetArtifactStore(str(Path(d) / "artifacts.db"))
        result = verify_artifact("ART-does-not-exist", artifact_store=art, historical_store=hist)
        assert result.found is False
        assert result.is_valid is False
        assert result.reasons == ("artifact_not_found",)


def test_verify_artifact_detects_data_that_changed_since_creation(stores):
    """The real integrity proof: create an artifact, then add a NEW
    ingestion run's worth of data spanning the same day (simulating
    later-arriving/corrected data), and confirm verify_artifact()
    honestly reports the fingerprint moved -- never silently reports
    success on drifted data."""
    hist, art = stores
    _write_full_session(hist, "2026-08-14T09:15:00+05:30")
    artifact = create_artifact("2026-08-14", "2026-08-14", historical_store=hist, artifact_store=art,
                                resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="15:15:00+05:30")

    # A later 5-minute bar lands for the same day, before the artifact's own as_of cutoff (15:15) --
    # a real "more data arrived for a window already covered" scenario.
    hist.write(_obs(SPOT_SYMBOL, "SPOT", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:20:00+05:30",
                     {"open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0}, run_id="RUN-spot-2"))

    result = verify_artifact(artifact.artifact_id, artifact_store=art, historical_store=hist)
    assert result.found is True
    assert result.fingerprint_unchanged is False
    assert result.is_valid is False
    assert "fingerprint_changed" in result.reasons


def test_verify_artifact_passes_when_nothing_changed(stores):
    hist, art = stores
    _write_full_session(hist, "2026-08-14T09:15:00+05:30")
    artifact = create_artifact("2026-08-14", "2026-08-14", historical_store=hist, artifact_store=art,
                                resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:15:00+05:30")
    result = verify_artifact(artifact.artifact_id, artifact_store=art, historical_store=hist)
    assert result.is_valid
    assert result.reasons == ()


# --- Regression: DatasetVersion / MarketRealitySnapshot / no-look-ahead unchanged --
def test_dataset_version_behavior_unchanged_by_this_phase(stores):
    hist, _ = stores
    _write_full_session(hist, "2026-08-14T09:15:00+05:30")
    dv = build_dataset_version("2026-08-14", "2026-08-14", historical_store=hist,
                                resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:15:00+05:30")
    assert dv.ready_dates == ("2026-08-14",)
    assert set(dv.included_components) == {"spot", "futures", "vix", "options"}
    assert len(dv.dataset_version_id) == 64  # unchanged hashing shape from Phase 18.7/18.10.


def test_no_look_ahead_preserved_via_artifact_creation(stores):
    hist, art = stores
    ce = f"{OPTIONS_UNDERLYING}|2026-08-18|21800|CE"
    hist.write(_obs(ce, "OPTION", RESOLUTION_FIVE_MINUTE, "2026-08-14T09:15:00+05:30",
                     {"ltp": 100.0}, value_kind=moc_taxonomy.VALUE_KIND_MAPPING))
    hist.write(_obs(ce, "OPTION", RESOLUTION_FIVE_MINUTE, "2026-08-14T15:25:00+05:30",
                     {"ltp": 999.0}, value_kind=moc_taxonomy.VALUE_KIND_MAPPING))

    artifact = create_artifact("2026-08-14", "2026-08-14", historical_store=hist, artifact_store=art,
                                resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:15:00+05:30")
    # Re-derive the actual snapshot content the artifact's own dataset_version_reference
    # corresponds to, and confirm the late (15:25) value never leaked in.
    from bujji.market_reality_snapshot.builder import build_market_reality_snapshot
    snap = build_market_reality_snapshot("2026-08-14", historical_store=hist,
                                          resolution=RESOLUTION_FIVE_MINUTE, as_of_time="2026-08-14T09:15:00+05:30")
    assert snap.options.contracts[0].ltp == 100.0
    assert snap.options.contracts[0].ltp != 999.0
    from bujji.market_reality_snapshot.dataset_version import build_dataset_version
    dv = build_dataset_version("2026-08-14", "2026-08-14", historical_store=hist,
                                resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:15:00+05:30")
    assert artifact.dataset_version_reference == dv.dataset_version_id


def test_no_reality_data_mutation_from_artifact_creation(stores):
    """Creating and verifying an artifact must never write to
    HistoricalObservationStore -- confirmed by row count before/after."""
    hist, art = stores
    _write_full_session(hist, "2026-08-14T09:15:00+05:30")
    before = hist.count()
    artifact = create_artifact("2026-08-14", "2026-08-14", historical_store=hist, artifact_store=art,
                                resolution=RESOLUTION_FIVE_MINUTE, as_of_time_of_day="09:15:00+05:30")
    verify_artifact(artifact.artifact_id, artifact_store=art, historical_store=hist)
    after = hist.count()
    assert before == after == 4  # spot + futures + vix + options, unchanged.
