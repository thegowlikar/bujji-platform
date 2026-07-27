"""Market Observation Contract v1 (MOC v1) — Engineering Series 73A.

Tests for `bujji/market_observation/`, the Observation-layer
implementation of `docs/MOF_V1_FOUNDATION.md`. Covers identity
determinism, serialization round-trip determinism, schema-version
compatibility, series ordering/gap detection, quality-metadata
independence from observation_id, provenance preservation, and an
AST-based isolation firewall mirroring this project's established
pattern (see tests/test_capital_brain.py's TestIsolation class).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bujji.market_observation import config, engine, models, query, runner, serialization, taxonomy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build(
    timestamp="2026-07-22T15:30:00+00:00",
    payload=24015.30,
    completeness=1.0,
    freshness=0.0,
    confidence=None,
    source="FYERS_HISTORICAL",
    instrument="NIFTY",
    observation_type=taxonomy.TYPE_PRICE,
    resolution=taxonomy.RESOLUTION_DAILY,
):
    return runner.build_observation(
        observation_type=observation_type,
        instrument=instrument,
        exchange="NSE",
        segment="INDEX",
        timestamp=timestamp,
        resolution=resolution,
        source=source,
        value_kind=taxonomy.VALUE_KIND_SCALAR,
        payload=payload,
        completeness=completeness,
        freshness=freshness,
        confidence=confidence,
        missing_fields=(),
        validation_status=taxonomy.VALIDATION_VALID,
        source_quality=taxonomy.SOURCE_QUALITY_HIGH,
        originating_source="FYERS",
        acquisition_timestamp="2026-07-22T15:30:05+00:00",
        normalization_timestamp="2026-07-22T15:30:06+00:00",
        origin=taxonomy.ORIGIN_LIVE,
    )


# ---------------------------------------------------------------------------
# Identity construction determinism
# ---------------------------------------------------------------------------
class TestIdentityDeterminism:
    def test_same_inputs_produce_same_observation_id(self):
        a = _build()
        b = _build()
        assert a.identity.observation_id == b.identity.observation_id

    def test_different_payload_produces_different_observation_id(self):
        a = _build(payload=24015.30)
        b = _build(payload=24020.00)
        assert a.identity.observation_id != b.identity.observation_id

    def test_different_timestamp_produces_different_observation_id(self):
        a = _build(timestamp="2026-07-22T15:30:00+00:00")
        b = _build(timestamp="2026-07-22T15:31:00+00:00")
        assert a.identity.observation_id != b.identity.observation_id

    def test_observation_id_never_uses_uuid4(self):
        a = _build()
        b = _build()
        # If this were uuid4-derived, two builds could never match.
        assert a.identity.observation_id == b.identity.observation_id
        assert a.identity.observation_id.startswith("OBS-")


# ---------------------------------------------------------------------------
# Serialization round-trip determinism
# ---------------------------------------------------------------------------
class TestSerializationRoundTrip:
    def test_observation_round_trip_exact(self):
        obs = _build()
        restored = serialization.observation_from_dict(serialization.observation_to_dict(obs))
        assert restored == obs

    def test_observation_json_round_trip_exact(self):
        obs = _build()
        restored = serialization.observation_from_json(serialization.observation_to_json(obs))
        assert restored == obs

    def test_series_round_trip_exact(self):
        series = engine.new_series(taxonomy.TYPE_PRICE, "NIFTY", taxonomy.RESOLUTION_DAILY)
        series = engine.append_observation(series, _build(timestamp="2026-07-20T15:30:00+00:00"))
        series = engine.append_observation(series, _build(timestamp="2026-07-21T15:30:00+00:00"))
        restored = serialization.series_from_dict(serialization.series_to_dict(series))
        assert restored == series

    def test_fingerprint_is_deterministic_across_calls(self):
        series = engine.new_series(taxonomy.TYPE_PRICE, "NIFTY", taxonomy.RESOLUTION_DAILY)
        series = engine.append_observation(series, _build())
        fp1 = serialization.series_fingerprint(series)
        fp2 = serialization.series_fingerprint(series)
        assert fp1 == fp2

    def test_fingerprint_changes_when_content_changes(self):
        series1 = engine.new_series(taxonomy.TYPE_PRICE, "NIFTY", taxonomy.RESOLUTION_DAILY)
        series1 = engine.append_observation(series1, _build(payload=24015.30))
        series2 = engine.new_series(taxonomy.TYPE_PRICE, "NIFTY", taxonomy.RESOLUTION_DAILY)
        series2 = engine.append_observation(series2, _build(payload=25000.00))
        assert serialization.series_fingerprint(series1) != serialization.series_fingerprint(series2)

    def test_serialized_dict_has_no_uuid4_or_wallclock_dependency(self):
        obs = _build()
        d1 = serialization.observation_to_dict(obs)
        d2 = serialization.observation_to_dict(_build())
        assert d1 == d2


# ---------------------------------------------------------------------------
# Schema-versioning backward-compatibility stub
# ---------------------------------------------------------------------------
class TestSchemaVersioning:
    def test_current_schema_version_is_recognized(self):
        assert config.SCHEMA_VERSION in taxonomy.RECOGNIZED_SCHEMA_VERSIONS

    def test_observation_built_with_current_schema_version_validates(self):
        obs = _build()
        result = runner.validate_observation(obs)
        assert result.is_valid

    def test_observation_with_mismatched_schema_version_fails_validation(self):
        obs = _build()
        stale_identity = models.ObservationIdentity(
            observation_id=obs.identity.observation_id,
            observation_type=obs.identity.observation_type,
            instrument=obs.identity.instrument,
            exchange=obs.identity.exchange,
            segment=obs.identity.segment,
            timestamp=obs.identity.timestamp,
            resolution=obs.identity.resolution,
            source=obs.identity.source,
            schema_version="0.0.1-stale",
        )
        stale_obs = models.Observation(
            identity=stale_identity, quality=obs.quality, provenance=obs.provenance, value=obs.value
        )
        result = runner.validate_observation(stale_obs)
        assert not result.is_valid
        assert "SCHEMA_VERSION_MISMATCH" in result.reasons


# ---------------------------------------------------------------------------
# ObservationSeries ordering and gap detection
# ---------------------------------------------------------------------------
class TestSeriesOrderingAndGaps:
    def test_append_preserves_insertion_order(self):
        series = engine.new_series(taxonomy.TYPE_PRICE, "NIFTY", taxonomy.RESOLUTION_DAILY)
        obs1 = _build(timestamp="2026-07-20T15:30:00+00:00")
        obs2 = _build(timestamp="2026-07-21T15:30:00+00:00")
        series = engine.append_observation(series, obs1)
        series = engine.append_observation(series, obs2)
        assert series.observations == (obs1, obs2)

    def test_ordered_append_is_valid(self):
        series = engine.new_series(taxonomy.TYPE_PRICE, "NIFTY", taxonomy.RESOLUTION_DAILY)
        series = engine.append_observation(series, _build(timestamp="2026-07-20T15:30:00+00:00"))
        series = engine.append_observation(series, _build(timestamp="2026-07-21T15:30:00+00:00"))
        result = engine.validate_series_ordering(series)
        assert result.is_valid

    def test_out_of_order_append_is_still_appended_not_dropped(self):
        series = engine.new_series(taxonomy.TYPE_PRICE, "NIFTY", taxonomy.RESOLUTION_DAILY)
        series = engine.append_observation(series, _build(timestamp="2026-07-21T15:30:00+00:00"))
        series = engine.append_observation(series, _build(timestamp="2026-07-20T15:30:00+00:00"))
        assert len(series.observations) == 2

    def test_out_of_order_append_records_explicit_gap_marker(self):
        series = engine.new_series(taxonomy.TYPE_PRICE, "NIFTY", taxonomy.RESOLUTION_DAILY)
        series = engine.append_observation(series, _build(timestamp="2026-07-21T15:30:00+00:00"))
        series = engine.append_observation(series, _build(timestamp="2026-07-20T15:30:00+00:00"))
        assert any(g.reason == taxonomy.GAP_REASON_OUT_OF_ORDER_SKIPPED for g in series.gaps)

    def test_out_of_order_append_fails_ordering_validation(self):
        series = engine.new_series(taxonomy.TYPE_PRICE, "NIFTY", taxonomy.RESOLUTION_DAILY)
        series = engine.append_observation(series, _build(timestamp="2026-07-21T15:30:00+00:00"))
        series = engine.append_observation(series, _build(timestamp="2026-07-20T15:30:00+00:00"))
        result = engine.validate_series_ordering(series)
        assert not result.is_valid

    def test_missing_interval_gap_detected_for_daily_resolution(self):
        series = engine.new_series(taxonomy.TYPE_PRICE, "NIFTY", taxonomy.RESOLUTION_DAILY)
        series = engine.append_observation(series, _build(timestamp="2026-07-10T15:30:00+00:00"))
        series = engine.append_observation(series, _build(timestamp="2026-07-20T15:30:00+00:00"))
        assert any(g.reason == taxonomy.GAP_REASON_MISSING_INTERVAL for g in series.gaps)

    def test_no_gap_for_normal_consecutive_daily_bars(self):
        series = engine.new_series(taxonomy.TYPE_PRICE, "NIFTY", taxonomy.RESOLUTION_DAILY)
        series = engine.append_observation(series, _build(timestamp="2026-07-20T15:30:00+00:00"))
        series = engine.append_observation(series, _build(timestamp="2026-07-21T15:30:00+00:00"))
        assert series.gaps == ()

    def test_query_at_or_before(self):
        series = engine.new_series(taxonomy.TYPE_PRICE, "NIFTY", taxonomy.RESOLUTION_DAILY)
        series = engine.append_observation(series, _build(timestamp="2026-07-20T15:30:00+00:00"))
        series = engine.append_observation(series, _build(timestamp="2026-07-22T15:30:00+00:00"))
        found = query.at_or_before(series, "2026-07-21T00:00:00+00:00")
        assert found is not None
        assert found.identity.timestamp == "2026-07-20T15:30:00+00:00"

    def test_query_in_window(self):
        series = engine.new_series(taxonomy.TYPE_PRICE, "NIFTY", taxonomy.RESOLUTION_DAILY)
        series = engine.append_observation(series, _build(timestamp="2026-07-19T15:30:00+00:00"))
        series = engine.append_observation(series, _build(timestamp="2026-07-20T15:30:00+00:00"))
        series = engine.append_observation(series, _build(timestamp="2026-07-25T15:30:00+00:00"))
        window = query.in_window(series, "2026-07-20T00:00:00+00:00", "2026-07-21T00:00:00+00:00")
        assert len(window) == 1
        assert window[0].identity.timestamp == "2026-07-20T15:30:00+00:00"

    def test_mismatched_instrument_append_raises(self):
        series = engine.new_series(taxonomy.TYPE_PRICE, "NIFTY", taxonomy.RESOLUTION_DAILY)
        other = _build(instrument="BANKNIFTY")
        with pytest.raises(ValueError):
            engine.append_observation(series, other)


# ---------------------------------------------------------------------------
# Quality metadata does NOT affect observation_id / identity equality
# ---------------------------------------------------------------------------
class TestQualityMetadataIndependence:
    def test_differing_quality_metadata_same_observation_id(self):
        a = _build(completeness=1.0, freshness=0.0, confidence=None)
        b = _build(completeness=0.4, freshness=120.0, confidence=0.7)
        assert a.identity.observation_id == b.identity.observation_id

    def test_differing_quality_metadata_produces_unequal_observation_objects(self):
        # The Observation as a whole differs (quality differs), but its
        # *identity* is the same fact -- this is the documented design:
        # quality is a read about the fact, not part of the fact.
        a = _build(completeness=1.0, freshness=0.0, confidence=None)
        b = _build(completeness=0.4, freshness=120.0, confidence=0.7)
        assert a.identity == b.identity
        assert a.quality != b.quality
        assert a != b


# ---------------------------------------------------------------------------
# Provenance fields preserved through serialization
# ---------------------------------------------------------------------------
class TestProvenancePreservation:
    def test_provenance_fields_survive_round_trip(self):
        obs = _build()
        restored = serialization.observation_from_dict(serialization.observation_to_dict(obs))
        assert restored.provenance == obs.provenance
        assert restored.provenance.originating_source == "FYERS"
        assert restored.provenance.origin == taxonomy.ORIGIN_LIVE

    def test_transformation_history_survives_round_trip_as_tuple(self):
        obs = runner.build_observation(
            observation_type=taxonomy.TYPE_PRICE,
            instrument="NIFTY",
            exchange="NSE",
            segment="INDEX",
            timestamp="2026-07-22T15:30:00+00:00",
            resolution=taxonomy.RESOLUTION_DAILY,
            source="FYERS_HISTORICAL",
            value_kind=taxonomy.VALUE_KIND_SCALAR,
            payload=24015.30,
            completeness=1.0,
            freshness=0.0,
            confidence=None,
            missing_fields=(),
            validation_status=taxonomy.VALIDATION_VALID,
            source_quality=taxonomy.SOURCE_QUALITY_HIGH,
            originating_source="FYERS",
            acquisition_timestamp="2026-07-22T15:30:05+00:00",
            normalization_timestamp="2026-07-22T15:30:06+00:00",
            origin=taxonomy.ORIGIN_LIVE,
            transformation_history=("BHAVCOPY_PARSE", "TIMEZONE_NORMALIZE"),
        )
        restored = serialization.observation_from_dict(serialization.observation_to_dict(obs))
        assert restored.provenance.transformation_history == ("BHAVCOPY_PARSE", "TIMEZONE_NORMALIZE")
        assert isinstance(restored.provenance.transformation_history, tuple)


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------
class TestStructuralValidation:
    def test_valid_observation_passes(self):
        assert runner.validate_observation(_build()).is_valid

    def test_missing_instrument_fails(self):
        obs = _build()
        bad_identity = models.ObservationIdentity(
            observation_id=obs.identity.observation_id,
            observation_type=obs.identity.observation_type,
            instrument="",
            exchange=obs.identity.exchange,
            segment=obs.identity.segment,
            timestamp=obs.identity.timestamp,
            resolution=obs.identity.resolution,
            source=obs.identity.source,
            schema_version=obs.identity.schema_version,
        )
        bad_obs = models.Observation(identity=bad_identity, quality=obs.quality, provenance=obs.provenance, value=obs.value)
        result = runner.validate_observation(bad_obs)
        assert not result.is_valid
        assert "MISSING_INSTRUMENT" in result.reasons

    def test_confidence_out_of_range_fails(self):
        obs = _build(confidence=1.5)
        result = runner.validate_observation(obs)
        assert not result.is_valid
        assert "CONFIDENCE_OUT_OF_RANGE" in result.reasons

    def test_confidence_none_is_valid_never_fabricated(self):
        obs = _build(confidence=None)
        result = runner.validate_observation(obs)
        assert result.is_valid
        assert obs.quality.confidence is None

    def test_completeness_out_of_range_fails(self):
        obs = _build(completeness=1.5)
        result = runner.validate_observation(obs)
        assert not result.is_valid
        assert "COMPLETENESS_OUT_OF_RANGE" in result.reasons

    def test_malformed_timestamp_fails(self):
        obs = _build(timestamp="not-a-timestamp")
        result = runner.validate_observation(obs)
        assert not result.is_valid
        assert "MALFORMED_TIMESTAMP" in result.reasons


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------
class TestImmutability:
    def test_observation_is_frozen(self):
        obs = _build()
        with pytest.raises(Exception):
            obs.value = models.ObservationValue(value_kind="SCALAR", payload=0)

    def test_series_is_frozen(self):
        series = engine.new_series(taxonomy.TYPE_PRICE, "NIFTY", taxonomy.RESOLUTION_DAILY)
        with pytest.raises(Exception):
            series.instrument = "BANKNIFTY"


# ---------------------------------------------------------------------------
# AST isolation firewall — mirrors tests/test_capital_brain.py's
# TestIsolation class style exactly (ast.parse + ast.walk over every
# module in the package, checking ast.Import / ast.ImportFrom nodes).
# ---------------------------------------------------------------------------
def _module_source_files():
    base = Path(engine.__file__).parent
    return list(base.glob("*.py"))


class TestIsolation:
    def test_no_mic_v2_import_anywhere(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "mic_v2" not in alias.name
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "mic_v2" not in node.module

    def test_no_forbidden_bujji_module_imports(self):
        forbidden = ("bujji.mic_replay", "bujji.production_runtime", "bujji.trading_brain")
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for f in forbidden:
                            assert f not in alias.name
                if isinstance(node, ast.ImportFrom) and node.module:
                    for f in forbidden:
                        assert f not in node.module

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")
                if isinstance(node, ast.Name) and node.id == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")
