"""Published-State Recorder — BUJJI Options OS v3, Engineering Series
62.

Records one immutable `PublishedStateRecord` per replay session,
capturing exactly what MIC v2's real publication pipeline produced.
Append-only, matching the same recorder discipline established in
Series 58/61.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .compatibility_validator import CompatibilityResult
from .publication_replay import PublishedState


def _deterministic_id(prefix: str, seed: str) -> str:
    return f"{prefix}-" + hashlib.md5(seed.encode()).hexdigest()[:16]


def publication_id(session_id: str, timestamp: str, publication_ids: Dict[str, Optional[str]]) -> str:
    ids_repr = "|".join(f"{k}={v}" for k, v in sorted(publication_ids.items()))
    return _deterministic_id("PUBID", f"{session_id}|{timestamp}|{ids_repr}")


@dataclass(frozen=True)
class PublishedStateRecord:
    replay_identifier: str
    evidence_identifier: str
    publication_identifier: str
    published_classifications: Dict[str, Optional[str]]
    compatibility: CompatibilityResult
    timestamp: str
    provenance: str
    qualification_fingerprint: str


class PublishedStateRecorder:
    """Append-only. `.records` always returns a fresh tuple snapshot --
    nothing already recorded can be overwritten or removed.
    """

    def __init__(self) -> None:
        self._records: list = []

    def record(
        self,
        replay_identifier: str,
        evidence_identifier: str,
        timestamp: str,
        state: PublishedState,
        compatibility: CompatibilityResult,
        provenance: str,
        qualification_fingerprint: str,
    ) -> PublishedStateRecord:
        classifications = {
            "market_context": state.market_context,
            "market_opinion": state.market_opinion,
            "context_stability": state.context_stability,
            "calibration": state.calibration,
            "governance": state.governance,
            "lifecycle": state.lifecycle,
            "contract": state.contract,
        }
        record = PublishedStateRecord(
            replay_identifier=replay_identifier,
            evidence_identifier=evidence_identifier,
            publication_identifier=publication_id(replay_identifier, timestamp, state.publication_ids),
            published_classifications=classifications,
            compatibility=compatibility,
            timestamp=timestamp,
            provenance=provenance,
            qualification_fingerprint=qualification_fingerprint,
        )
        self._records.append(record)
        return record

    @property
    def records(self) -> Tuple[PublishedStateRecord, ...]:
        return tuple(self._records)

    def __len__(self) -> int:
        return len(self._records)
