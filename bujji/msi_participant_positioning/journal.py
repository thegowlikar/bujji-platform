"""ParticipantPositioningJournal — append-only audit trail, mirroring
bujji.msi_market_direction.journal's conventions. Never modifies or
deletes a prior entry; no wall-clock reads (caller supplies
`recorded_at`)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .models import MarketParticipantPositioningAssessment
from .serialization import assessment_to_dict


@dataclass(frozen=True)
class JournalEntry:
    kind: str
    recorded_at: str
    payload: dict


class ParticipantPositioningJournal:
    def __init__(self) -> None:
        self._entries: List[JournalEntry] = []

    def record_assessment(self, assessment: MarketParticipantPositioningAssessment, *, recorded_at: str) -> None:
        self._entries.append(JournalEntry(
            kind="assessment_recorded", recorded_at=recorded_at, payload=assessment_to_dict(assessment),
        ))

    def entries(self) -> Tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
