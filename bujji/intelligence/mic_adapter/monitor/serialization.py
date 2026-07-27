"""ObservationHealth <-> JSON serialization — BUJJI Options OS,
Integration Series 2, Sprint 2. Independent schema -- explicit,
hand-written, versioned, round-trip deterministic.
"""
from __future__ import annotations

from datetime import datetime

from .health import ObservationHealth
from .metrics import ObservationProvenance

SCHEMA_VERSION = "1.0.0"


def observation_health_to_dict(health: ObservationHealth) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "health_id": health.health_id,
        "timestamp": health.timestamp.isoformat(),
        "health_status": health.health_status,
        "feature_flag_enabled": health.feature_flag_enabled,
        "snapshot_age_seconds": health.snapshot_age_seconds,
        "latency_seconds": health.latency_seconds,
        "reason": health.reason,
        "provenance": {
            "source": health.provenance.source,
            "ruleset_version": health.provenance.ruleset_version,
        },
    }


def observation_health_from_dict(data: dict) -> ObservationHealth:
    prov = data["provenance"]
    return ObservationHealth(
        health_id=data["health_id"],
        timestamp=datetime.fromisoformat(data["timestamp"]),
        health_status=data["health_status"],
        feature_flag_enabled=data["feature_flag_enabled"],
        snapshot_age_seconds=data["snapshot_age_seconds"],
        latency_seconds=data["latency_seconds"],
        reason=data["reason"],
        provenance=ObservationProvenance(source=prov["source"], ruleset_version=prov["ruleset_version"]),
    )
