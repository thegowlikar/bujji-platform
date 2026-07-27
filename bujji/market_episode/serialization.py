"""Deterministic JSON round-trip for Episode, reusing MOC v1/LMEE's
serialization conventions (explicit key ordering, no uuid4, no wall
clock).
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import Episode, EpisodeProvenance


def provenance_to_dict(provenance: EpisodeProvenance) -> Dict[str, Any]:
    return {
        "originating_source": provenance.originating_source,
        "detection_context": provenance.detection_context,
        "schema_version": provenance.schema_version,
    }


def provenance_from_dict(d: Dict[str, Any]) -> EpisodeProvenance:
    return EpisodeProvenance(
        originating_source=d["originating_source"],
        detection_context=d["detection_context"],
        schema_version=d["schema_version"],
    )


def episode_to_dict(episode: Episode) -> Dict[str, Any]:
    return {
        "episode_id": episode.episode_id,
        "episode_type": episode.episode_type,
        "start_time": episode.start_time,
        "latest_update": episode.latest_update,
        "end_time": episode.end_time,
        "originating_event_ids": list(episode.originating_event_ids),
        "originating_observation_ids": list(episode.originating_observation_ids),
        "current_state": episode.current_state,
        "provenance": provenance_to_dict(episode.provenance),
        "schema_version": episode.schema_version,
    }


def episode_from_dict(d: Dict[str, Any]) -> Episode:
    return Episode(
        episode_id=d["episode_id"],
        episode_type=d["episode_type"],
        start_time=d["start_time"],
        latest_update=d["latest_update"],
        end_time=d.get("end_time"),
        originating_event_ids=tuple(d["originating_event_ids"]),
        originating_observation_ids=tuple(d["originating_observation_ids"]),
        current_state=d["current_state"],
        provenance=provenance_from_dict(d["provenance"]),
        schema_version=d["schema_version"],
    )


def episode_to_json(episode: Episode) -> str:
    return json.dumps(episode_to_dict(episode), sort_keys=True, default=repr)


def episode_from_json(text: str) -> Episode:
    return episode_from_dict(json.loads(text))
