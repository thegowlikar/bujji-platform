"""ShadowTradingJournal — append-only audit trail, mirroring every
prior MSI package's journal convention. Deliverable 6: records the
full Entry -> Lifecycle events -> Exit -> PnL sequence for every
simulated trade, one entry per day's tracking update."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .models import ShadowPosition
from .serialization import position_to_dict


@dataclass(frozen=True)
class JournalEntry:
    kind: str
    recorded_at: str
    payload: dict


class ShadowTradingJournal:
    def __init__(self) -> None:
        self._entries: List[JournalEntry] = []

    def record_open(self, position: ShadowPosition, *, recorded_at: str) -> None:
        self._entries.append(JournalEntry(kind="shadow_position_opened", recorded_at=recorded_at,
                                          payload=position_to_dict(position)))

    def record_tracking_update(self, position: ShadowPosition, *, recorded_at: str) -> None:
        kind = "shadow_position_closed" if position.completed else "shadow_position_tracked"
        self._entries.append(JournalEntry(kind=kind, recorded_at=recorded_at, payload=position_to_dict(position)))

    def entries(self) -> Tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
