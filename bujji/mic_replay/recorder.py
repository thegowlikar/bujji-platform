"""Historical Intelligence Recorder — BUJJI Options OS v3, Engineering
Series 61.

Records one immutable `HistoricalIntelligenceRecord` per replay
session, capturing exactly what MIC v2's replay produced for that
session -- never a summary, never a fabricated substitute. Append-only,
matching the same recorder discipline established in Series 58.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


def _deterministic_id(prefix: str, seed: str) -> str:
    return f"{prefix}-" + hashlib.md5(seed.encode()).hexdigest()[:16]


def observation_id(session_id: str, timestamp: str) -> str:
    return _deterministic_id("OBS", f"{session_id}|{timestamp}")


def intelligence_id(session_id: str, timestamp: str, evidence_count: int) -> str:
    return _deterministic_id("INTEL", f"{session_id}|{timestamp}|{evidence_count}")


@dataclass(frozen=True)
class HistoricalIntelligenceRecord:
    replay_identifier: str
    observation_identifier: str
    intelligence_identifier: str
    timestamp: str
    mic_outputs: Tuple[Dict[str, Any], ...]
    provenance: str
    qualification_fingerprint: str


class HistoricalIntelligenceRecorder:
    """Append-only. `.records` always returns a fresh tuple snapshot --
    nothing already recorded can be overwritten or removed.
    """

    def __init__(self) -> None:
        self._records: list = []

    def record(
        self,
        replay_identifier: str,
        timestamp: str,
        mic_outputs: Tuple[Dict[str, Any], ...],
        provenance: str,
        qualification_fingerprint: str,
    ) -> HistoricalIntelligenceRecord:
        record = HistoricalIntelligenceRecord(
            replay_identifier=replay_identifier,
            observation_identifier=observation_id(replay_identifier, timestamp),
            intelligence_identifier=intelligence_id(replay_identifier, timestamp, len(mic_outputs)),
            timestamp=timestamp,
            mic_outputs=tuple(mic_outputs),
            provenance=provenance,
            qualification_fingerprint=qualification_fingerprint,
        )
        self._records.append(record)
        return record

    @property
    def records(self) -> Tuple[HistoricalIntelligenceRecord, ...]:
        return tuple(self._records)

    def __len__(self) -> int:
        return len(self._records)
