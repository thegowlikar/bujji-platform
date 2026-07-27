"""MarketDirectionJournal — append-only audit trail, mirroring
bujji.msi_consensus.journal / bujji.msi_price_structure.journal's
conventions. Never modifies or deletes a prior entry; deterministic
ordering; no wall-clock reads (the caller supplies `recorded_at`)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from .models import MarketDirectionAssessment
from .serialization import assessment_to_dict


@dataclass(frozen=True)
class JournalEntry:
    kind: str                # "assessment_recorded"
    recorded_at: str
    payload: dict


class MarketDirectionJournal:
    """Append-only. `entries()` returns an immutable snapshot tuple;
    nothing in this class ever mutates a previously-appended entry."""

    def __init__(self) -> None:
        self._entries: List[JournalEntry] = []

    def record_assessment(self, assessment: MarketDirectionAssessment, *, recorded_at: str) -> None:
        self._entries.append(JournalEntry(
            kind="assessment_recorded",
            recorded_at=recorded_at,
            payload=assessment_to_dict(assessment),
        ))

    def entries(self) -> Tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
