"""Market Observation Contract journal — deterministic, replay-safe
audit trail for observation/series operations.

Mirrors this project's established journal pattern (see
`bujji/journal/runtime_safety_journal.py`, and the `ShadowJournal`
concept referenced for pattern purposes in /opt/bujji-mic-v2, read-only,
never imported from): append-only JSONL, own schema, versioned, own
storage path, entirely independent of every other journal in the
codebase. This journal only ever records already-produced Observation /
ObservationSeries instances handed to it by runner.py -- it performs no
construction, no validation, and no decision of its own.

No wall-clock is read here unless a timestamp is explicitly passed in
by the caller (this module does not call datetime.now() anywhere) --
replay-safety depends on this.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from .models import Observation, ObservationSeries
from .serialization import (
    observation_from_dict,
    observation_to_dict,
    series_from_dict,
    series_to_dict,
)

SCHEMA_VERSION = "1.0.0"


class MarketObservationJournal:
    """Append-only JSONL journal for Observation / ObservationSeries
    records. Two record kinds share one file, distinguished by a
    `"kind"` field, so a single journal instance can capture a full
    build-then-append lifecycle in call order -- the recorded order
    itself is part of the audit trail.
    """

    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record_observation(self, observation: Observation) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "OBSERVATION",
            "observation": observation_to_dict(observation),
        }
        self._append(payload)

    def record_series(self, series: ObservationSeries) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "SERIES",
            "series": series_to_dict(series),
        }
        self._append(payload)

    def _append(self, payload: dict) -> None:
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload, sort_keys=True) + "\n")

    def read_all(self) -> List[Union[Observation, ObservationSeries]]:
        if not self._path.exists():
            return []
        out: List[Union[Observation, ObservationSeries]] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if payload["kind"] == "OBSERVATION":
                    out.append(observation_from_dict(payload["observation"]))
                elif payload["kind"] == "SERIES":
                    out.append(series_from_dict(payload["series"]))
        return out
