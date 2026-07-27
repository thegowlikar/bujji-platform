"""Deterministic JSON round-trip for MarketEvent, reusing MOC v1's
serialization primitives' conventions (explicit key ordering, no
uuid4, no wall clock).
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import MarketEvent, MarketEventProvenance


def provenance_to_dict(provenance: MarketEventProvenance) -> Dict[str, Any]:
    return {
        "originating_source": provenance.originating_source,
        "detection_context": provenance.detection_context,
        "schema_version": provenance.schema_version,
    }


def provenance_from_dict(d: Dict[str, Any]) -> MarketEventProvenance:
    return MarketEventProvenance(
        originating_source=d["originating_source"],
        detection_context=d["detection_context"],
        schema_version=d["schema_version"],
    )


def event_to_dict(event: MarketEvent) -> Dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "timestamp": event.timestamp,
        "originating_observation_ids": list(event.originating_observation_ids),
        "detail": dict(event.detail),
        "provenance": provenance_to_dict(event.provenance),
        "schema_version": event.schema_version,
    }


def event_from_dict(d: Dict[str, Any]) -> MarketEvent:
    return MarketEvent(
        event_id=d["event_id"],
        event_type=d["event_type"],
        timestamp=d["timestamp"],
        originating_observation_ids=tuple(d["originating_observation_ids"]),
        detail=dict(d["detail"]),
        provenance=provenance_from_dict(d["provenance"]),
        schema_version=d["schema_version"],
    )


def event_to_json(event: MarketEvent) -> str:
    return json.dumps(event_to_dict(event), sort_keys=True, default=repr)


def event_from_json(text: str) -> MarketEvent:
    return event_from_dict(json.loads(text))
