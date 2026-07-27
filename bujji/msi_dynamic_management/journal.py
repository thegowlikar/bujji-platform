"""bujji.msi_dynamic_management.journal — Series 109.

Append-only, in-memory -- mirrors this project's own established
journal convention (`entries()`/`record_*`/`__len__`)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Tuple


@dataclass(frozen=True)
class JournalEntry:
    day: str
    lifecycle_id: str
    board: Any


class DynamicManagementJournal:
    def __init__(self) -> None:
        self._entries: List[JournalEntry] = []

    def record_board(self, day: str, lifecycle_id: str, board: Any) -> None:
        self._entries.append(JournalEntry(day=day, lifecycle_id=lifecycle_id, board=board))

    def entries(self) -> Tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
