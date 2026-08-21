"""Phase 17H.3/17H.4 — HistoricalObservation contract.

Mirrors test_market_reality_store.py's own posture: real construction,
real round-trips, no mocking of the models themselves.
"""
from bujji.historical_reality.capture import (
    TRANSFORMATION_HISTORICAL_INGESTION,
    build_historical_observation,
)
from bujji.historical_reality.models import (
    HistoricalLineage,
    HistoricalObservation,
    IngestionRun,
    RUN_STATUS_ERROR,
    RUN_STATUS_NO_DATA,
    RUN_STATUS_OK,
)
from bujji.market_observation import taxonomy as moc_taxonomy

NOW = "2026-08-13T16:52:00+05:30"


def _obs(**overrides):
    kwargs = dict(
        instrument_identity="NSE:NIFTY50-INDEX",
        instrument_type="SPOT",
        resolution=moc_taxonomy.RESOLUTION_DAILY,
        timestamp="1998-05-04T09:15:00+05:30",
        payload={"open": 1159.8, "high": 1185.75, "low": 1159.8, "close": 1185.15, "volume": None},
        source="fyers_historical",
        access_method="direct_sdk_fyers_historical_rest",
        source_epoch=894240000,
        source_symbol="NSE:NIFTY50-INDEX",
        raw_artifact_ref="raw_artifacts/fyers/NSE_NIFTY50-INDEX/daily/1998.json",
        ingestion_run_id="RUN-test",
        retrieved_at=NOW,
        certification_status="CERTIFIED_AVAILABLE",
        certification_ref="fyers_nifty_spot_historical_certification_20260813.json@ts",
    )
    kwargs.update(overrides)
    return build_historical_observation(**kwargs)


# --- Construction ------------------------------------------------------------
def test_build_historical_observation_shape():
    obs = _obs()
    assert obs.instrument == "NSE:NIFTY50-INDEX"
    assert obs.instrument_type == "SPOT"
    assert obs.payload == {"open": 1159.8, "high": 1185.75, "low": 1159.8,
                            "close": 1185.15, "volume": None}
    assert obs.observation.identity.resolution == moc_taxonomy.RESOLUTION_DAILY
    assert obs.observation.identity.timestamp == "1998-05-04T09:15:00+05:30"
    assert obs.observation.provenance.origin == moc_taxonomy.ORIGIN_HISTORICAL_RECONSTRUCTION
    assert obs.observation.value.value_kind == moc_taxonomy.VALUE_KIND_OHLC
    assert obs.observation.provenance.transformation_history == (TRANSFORMATION_HISTORICAL_INGESTION,)


def test_observation_id_is_deterministic_content_hash():
    """Same fact -> same id. Different content -> different id. Minted
    through the SAME market_observation.engine.build_observation() Layer
    0 already uses -- not a separate hash scheme."""
    a = _obs()
    b = _obs()
    assert a.observation_id == b.observation_id

    c = _obs(payload={"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": None})
    assert c.observation_id != a.observation_id


def test_lineage_carries_historical_specific_provenance():
    obs = _obs()
    assert obs.lineage.source == "fyers_historical"
    assert obs.lineage.access_method == "direct_sdk_fyers_historical_rest"
    assert obs.lineage.source_epoch == 894240000
    assert obs.lineage.source_symbol == "NSE:NIFTY50-INDEX"
    assert obs.lineage.ingestion_run_id == "RUN-test"
    assert obs.lineage.certification_status == "CERTIFIED_AVAILABLE"


def test_continuity_method_defaults_to_none_for_spot():
    """Spot has no continuous-vs-specific-contract distinction (that's
    futures-only, PHASE_17H3 Part 2.4) -- must default to None, never a
    fabricated value."""
    assert _obs().lineage.continuity_method is None


def test_continuity_method_is_settable_for_futures_continuous_series():
    obs = _obs(instrument_identity="NIFTY_FUT_CONTINUOUS", instrument_type="FUTURE",
                continuity_method="fyers_cont_flag_1", source_symbol="NSE:NIFTY26AUGFUT")
    assert obs.lineage.continuity_method == "fyers_cont_flag_1"
    # The corrected futures-identity rule (PHASE_17H3 Part 2.4): identity is
    # NEVER the request symbol.
    assert obs.instrument == "NIFTY_FUT_CONTINUOUS"
    assert obs.lineage.source_symbol == "NSE:NIFTY26AUGFUT"
    assert obs.instrument != obs.lineage.source_symbol


# --- Round-trip serialization -------------------------------------------------
def test_historical_observation_round_trips_through_dict():
    obs = _obs()
    restored = HistoricalObservation.from_dict(obs.to_dict())
    assert restored.observation_id == obs.observation_id
    assert restored.instrument == obs.instrument
    assert restored.payload == obs.payload
    assert restored.lineage.source_epoch == obs.lineage.source_epoch
    assert restored.lineage.certification_ref == obs.lineage.certification_ref


def test_historical_lineage_round_trips_through_dict():
    lineage = HistoricalLineage(
        source="fyers_historical", access_method="direct_sdk_fyers_historical_rest",
        source_epoch=894240000, source_symbol="NSE:NIFTY50-INDEX",
        raw_artifact_ref="x.json", ingestion_run_id="RUN-1", retrieved_at=NOW,
        certification_status="CERTIFIED_AVAILABLE",
    )
    restored = HistoricalLineage.from_dict(lineage.to_dict())
    assert restored == lineage


# --- IngestionRun --------------------------------------------------------------
def test_ingestion_run_round_trips_and_distinguishes_statuses():
    """NO_DATA, ERROR, and OK must remain distinguishable facts -- never
    collapsed into "we got nothing" (PHASE_17H2 §2.3 / PHASE_17H3 §3.3)."""
    for status in (RUN_STATUS_OK, RUN_STATUS_NO_DATA, RUN_STATUS_ERROR):
        run = IngestionRun(
            ingestion_run_id=f"RUN-{status}", source="fyers_historical",
            instrument="NSE:NIFTY50-INDEX", resolution=moc_taxonomy.RESOLUTION_DAILY,
            range_from="1996-01-01", range_to="1996-12-31", started_at=NOW,
            status=status, rows_returned=0, raw_artifact_path="x.json",
        )
        restored = IngestionRun.from_dict(run.to_dict())
        assert restored.status == status


def test_ingestion_run_preserves_real_error_code():
    """A rejected >366-day request (code=-50, PHASE_17H1 §1.1) must be
    a recorded, real fact, not lost console output."""
    run = IngestionRun(
        ingestion_run_id="RUN-err", source="fyers_historical", instrument="NSE:NIFTY50-INDEX",
        resolution=moc_taxonomy.RESOLUTION_DAILY, range_from="2020-01-01", range_to="2026-01-01",
        started_at=NOW, status=RUN_STATUS_ERROR, rows_returned=0,
        raw_artifact_path="x.json", error_code=-50, error_message="Invalid input",
    )
    restored = IngestionRun.from_dict(run.to_dict())
    assert restored.error_code == -50
    assert restored.error_message == "Invalid input"
