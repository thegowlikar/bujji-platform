"""Deterministic JSON round-trip and fingerprinting for Observation /
ObservationSeries.

Explicit key ordering throughout -- never relies on dict iteration
order for anything that must be reproducible (fingerprinting in
particular). No uuid4(), no datetime.now(), no unseeded randomness
anywhere in this module.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

from .models import (
    Observation,
    ObservationIdentity,
    ObservationProvenance,
    ObservationQualityMetadata,
    ObservationSeries,
    ObservationValue,
    SeriesGap,
)


def identity_to_dict(identity: ObservationIdentity) -> Dict[str, Any]:
    return {
        "observation_id": identity.observation_id,
        "observation_type": identity.observation_type,
        "instrument": identity.instrument,
        "exchange": identity.exchange,
        "segment": identity.segment,
        "timestamp": identity.timestamp,
        "resolution": identity.resolution,
        "source": identity.source,
        "schema_version": identity.schema_version,
    }


def identity_from_dict(d: Dict[str, Any]) -> ObservationIdentity:
    return ObservationIdentity(
        observation_id=d["observation_id"],
        observation_type=d["observation_type"],
        instrument=d["instrument"],
        exchange=d["exchange"],
        segment=d["segment"],
        timestamp=d["timestamp"],
        resolution=d["resolution"],
        source=d["source"],
        schema_version=d["schema_version"],
    )


def quality_to_dict(quality: ObservationQualityMetadata) -> Dict[str, Any]:
    return {
        "completeness": quality.completeness,
        "freshness": quality.freshness,
        "confidence": quality.confidence,
        "missing_fields": list(quality.missing_fields),
        "validation_status": quality.validation_status,
        "source_quality": quality.source_quality,
    }


def quality_from_dict(d: Dict[str, Any]) -> ObservationQualityMetadata:
    return ObservationQualityMetadata(
        completeness=d["completeness"],
        freshness=d["freshness"],
        confidence=d.get("confidence"),
        missing_fields=tuple(d["missing_fields"]),
        validation_status=d["validation_status"],
        source_quality=d["source_quality"],
    )


def provenance_to_dict(provenance: ObservationProvenance) -> Dict[str, Any]:
    return {
        "originating_source": provenance.originating_source,
        "acquisition_timestamp": provenance.acquisition_timestamp,
        "normalization_timestamp": provenance.normalization_timestamp,
        "origin": provenance.origin,
        "version": provenance.version,
        "transformation_history": list(provenance.transformation_history),
    }


def provenance_from_dict(d: Dict[str, Any]) -> ObservationProvenance:
    return ObservationProvenance(
        originating_source=d["originating_source"],
        acquisition_timestamp=d["acquisition_timestamp"],
        normalization_timestamp=d["normalization_timestamp"],
        origin=d["origin"],
        version=d["version"],
        transformation_history=tuple(d["transformation_history"]),
    )


def value_to_dict(value: ObservationValue) -> Dict[str, Any]:
    return {
        "value_kind": value.value_kind,
        "payload": value.payload,
    }


def value_from_dict(d: Dict[str, Any]) -> ObservationValue:
    return ObservationValue(value_kind=d["value_kind"], payload=d["payload"])


def observation_to_dict(observation: Observation) -> Dict[str, Any]:
    return {
        "identity": identity_to_dict(observation.identity),
        "quality": quality_to_dict(observation.quality),
        "provenance": provenance_to_dict(observation.provenance),
        "value": value_to_dict(observation.value),
    }


def observation_from_dict(d: Dict[str, Any]) -> Observation:
    return Observation(
        identity=identity_from_dict(d["identity"]),
        quality=quality_from_dict(d["quality"]),
        provenance=provenance_from_dict(d["provenance"]),
        value=value_from_dict(d["value"]),
    )


def gap_to_dict(gap: SeriesGap) -> Dict[str, Any]:
    return {
        "after_timestamp": gap.after_timestamp,
        "before_timestamp": gap.before_timestamp,
        "reason": gap.reason,
    }


def gap_from_dict(d: Dict[str, Any]) -> SeriesGap:
    return SeriesGap(
        after_timestamp=d["after_timestamp"],
        before_timestamp=d["before_timestamp"],
        reason=d["reason"],
    )


def series_to_dict(series: ObservationSeries) -> Dict[str, Any]:
    return {
        "observation_type": series.observation_type,
        "instrument": series.instrument,
        "resolution": series.resolution,
        "window_start": series.window_start,
        "window_end": series.window_end,
        "observations": [observation_to_dict(o) for o in series.observations],
        "gaps": [gap_to_dict(g) for g in series.gaps],
    }


def series_from_dict(d: Dict[str, Any]) -> ObservationSeries:
    return ObservationSeries(
        observation_type=d["observation_type"],
        instrument=d["instrument"],
        resolution=d["resolution"],
        window_start=d["window_start"],
        window_end=d["window_end"],
        observations=tuple(observation_from_dict(o) for o in d["observations"]),
        gaps=tuple(gap_from_dict(g) for g in d["gaps"]),
    )


def observation_to_json(observation: Observation) -> str:
    return json.dumps(observation_to_dict(observation), sort_keys=True)


def observation_from_json(text: str) -> Observation:
    return observation_from_dict(json.loads(text))


def series_to_json(series: ObservationSeries) -> str:
    return json.dumps(series_to_dict(series), sort_keys=True)


def series_from_json(text: str) -> ObservationSeries:
    return series_from_dict(json.loads(text))


def series_fingerprint(series: ObservationSeries) -> str:
    """Deterministic content hash over an entire ObservationSeries.

    `sort_keys=True` guarantees stable key ordering regardless of dict
    construction order; the explicit `series_to_dict`/`observation_to_dict`
    field lists above additionally guarantee no field is silently
    dropped or reordered between runs. No wall-clock, no randomness.
    """
    canonical = json.dumps(series_to_dict(series), sort_keys=True, separators=(",", ":"))
    return "MOCFP-" + hashlib.md5(canonical.encode()).hexdigest()
