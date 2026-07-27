"""Options Observation Domain journal — deterministic, replay-safe audit
trail for options observation/series operations, Engineering Series
73C.

Mirrors `bujji/futures_observation/journal.py`'s established pattern:
append-only JSONL, own schema, versioned, own storage path, entirely
independent of every other journal in the codebase. Records
already-produced OptionObservation / OptionObservationSeries instances
handed to it by runner.py -- performs no construction, no validation,
no decision of its own. Also records validation failures, duplicate
detections, and ordering anomalies explicitly, so this domain's own
gap/anomaly disclosures are queryable, not just MOC's generic
SeriesGap markers.

No wall-clock is read here unless a timestamp is explicitly passed in
by the caller -- this module never calls datetime.now().
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Union

from .models import OptionObservation, OptionObservationSeries
from .serialization import (
    option_observation_from_dict,
    option_observation_to_dict,
    option_series_from_dict,
    option_series_to_dict,
)

SCHEMA_VERSION = "1.0.0"


class OptionsObservationJournal:
    """Append-only JSONL journal for OptionObservation /
    OptionObservationSeries records plus options-domain anomaly records
    (validation failures, duplicates, gaps, schema mismatches, ordering
    anomalies)."""

    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record_observation(self, option_observation: OptionObservation) -> None:
        self._append(
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "OPTION_OBSERVATION",
                "option_observation": option_observation_to_dict(option_observation),
            }
        )

    def record_series(self, option_series: OptionObservationSeries) -> None:
        self._append(
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "OPTION_SERIES",
                "option_series": option_series_to_dict(option_series),
            }
        )

    def record_validation_failure(self, option_observation: OptionObservation, reasons: tuple) -> None:
        self._append(
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "VALIDATION_FAILURE",
                "observation_id": option_observation.observation_id,
                "reasons": list(reasons),
            }
        )

    def record_duplicate(self, observation_id: str) -> None:
        self._append(
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "DUPLICATE_DETECTED",
                "observation_id": observation_id,
            }
        )

    def record_schema_mismatch(self, observation_id: str, expected: str, actual: str) -> None:
        self._append(
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "SCHEMA_MISMATCH",
                "observation_id": observation_id,
                "expected_schema_version": expected,
                "actual_schema_version": actual,
            }
        )

    def record_ordering_anomaly(self, after_timestamp: str, before_timestamp: str, reason: str) -> None:
        self._append(
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "ORDERING_ANOMALY",
                "after_timestamp": after_timestamp,
                "before_timestamp": before_timestamp,
                "reason": reason,
            }
        )

    def _append(self, payload: dict) -> None:
        with open(self._path, "a") as fh:  # Append-only, explicitly.
            fh.write(json.dumps(payload, sort_keys=True) + "\n")

    def read_all(self) -> List[Union[OptionObservation, OptionObservationSeries, dict]]:
        if not self._path.exists():
            return []
        out: List[Union[OptionObservation, OptionObservationSeries, dict]] = []
        with open(self._path, "r") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                kind = payload["kind"]
                if kind == "OPTION_OBSERVATION":
                    out.append(option_observation_from_dict(payload["option_observation"]))
                elif kind == "OPTION_SERIES":
                    out.append(option_series_from_dict(payload["option_series"]))
                else:
                    out.append(payload)
        return out
