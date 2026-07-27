"""Live Observation Producer Framework journal — deterministic,
replay-safe audit trail for events, translation outcomes, lifecycle
transitions, aggregation window closes, and errors.

Mirrors `bujji.market_observation.journal`'s established pattern:
append-only JSONL, own schema, own storage path, entirely independent
of every other journal in the codebase. No wall-clock is read here
unless a timestamp is explicitly passed in by the caller.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from bujji.market_observation.serialization import observation_to_dict

from .models import AggregationWindow, LiveObservationEvent, ProducerState
from .serialization import (
    event_to_dict,
    producer_state_to_dict,
    window_to_dict,
)

SCHEMA_VERSION = "1.0.0"


class LiveObservationJournal:
    """Append-only JSONL journal. Record kinds share one file,
    distinguished by a `"kind"` field, so a single journal instance
    captures a full event->translation->transition->window-close
    lifecycle in call order."""

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record_event(self, event: LiveObservationEvent) -> None:
        self._append({"schema_version": SCHEMA_VERSION, "kind": "EVENT", "event": event_to_dict(event)})

    def record_translation(self, event: LiveObservationEvent, observation) -> None:
        self._append({
            "schema_version": SCHEMA_VERSION,
            "kind": "TRANSLATION",
            "event": event_to_dict(event),
            "observation": observation_to_dict(observation) if observation is not None else None,
        })

    def record_transition(self, state: ProducerState) -> None:
        self._append({
            "schema_version": SCHEMA_VERSION,
            "kind": "PRODUCER_STATE",
            "state": producer_state_to_dict(state),
        })

    def record_window_close(self, window: AggregationWindow, observation) -> None:
        self._append({
            "schema_version": SCHEMA_VERSION,
            "kind": "WINDOW_CLOSE",
            "window": window_to_dict(window),
            "observation": observation_to_dict(observation) if observation is not None else None,
        })

    def record_error(self, event: LiveObservationEvent, error: str) -> None:
        self._append({
            "schema_version": SCHEMA_VERSION,
            "kind": "ERROR",
            "event": event_to_dict(event),
            "error": error,
        })

    def _append(self, payload: dict) -> None:
        with open(self._path, "a") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")

    def read_all(self) -> List[dict]:
        if not self._path.exists():
            return []
        out: List[dict] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
        return out
