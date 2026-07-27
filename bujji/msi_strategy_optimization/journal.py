"""bujji.msi_strategy_optimization.journal — Series 108.

Append-only, in-memory -- mirrors this project's own established journal
convention (`entries()`/`record_*`/`__len__`)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Tuple


@dataclass(frozen=True)
class JournalEntry:
    day: str
    strategy_family: str
    result: Any  # the dict returned by runner.run_optimization_tail


class OptimizationJournal:
    def __init__(self) -> None:
        self._entries: List[JournalEntry] = []

    def record_optimization(self, day: str, strategy_family: str, result: Any) -> None:
        self._entries.append(JournalEntry(day=day, strategy_family=strategy_family, result=result))

    def entries(self) -> Tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
