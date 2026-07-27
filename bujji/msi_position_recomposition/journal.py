"""bujji.msi_position_recomposition.journal — Series 110.

Append-only, in-memory -- mirrors this project's own established
journal convention."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Tuple


@dataclass(frozen=True)
class JournalEntry:
    day: str
    strategy_family: str
    assessment: Any


class RecompositionJournal:
    def __init__(self) -> None:
        self._entries: List[JournalEntry] = []

    def record(self, day: str, strategy_family: str, assessment: Any) -> None:
        self._entries.append(JournalEntry(day=day, strategy_family=strategy_family, assessment=assessment))

    def entries(self) -> Tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
