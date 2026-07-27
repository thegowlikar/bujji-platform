"""StrategySelectionFoundationJournal — append-only audit trail,
mirroring every prior MSI package's journal convention."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .models import StrategySuitabilityAssessment
from .serialization import assessment_to_dict


@dataclass(frozen=True)
class JournalEntry:
    kind: str
    recorded_at: str
    payload: dict


class StrategySelectionFoundationJournal:
    def __init__(self) -> None:
        self._entries: List[JournalEntry] = []

    def record_assessment(self, assessment: StrategySuitabilityAssessment, *, recorded_at: str) -> None:
        self._entries.append(JournalEntry(
            kind="assessment_recorded", recorded_at=recorded_at, payload=assessment_to_dict(assessment),
        ))

    def entries(self) -> Tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
