"""DecisionAuditorJournal — append-only audit trail.

Deliverable 1 finding: `bujji/journal/decision_journal.py::DecisionJournal`
already establishes the exact right PHILOSOPHY for this kind of
recorder ("Not Learning. Not analysis. Not a decision input... a
failure to persist a snapshot must never block or alter a live trading
decision") -- but its schema (`DecisionSnapshot`) belongs to the older,
single-strategy production_runtime generation, not this MSI arc. This
journal reuses that PHILOSOPHY and this whole arc's own established
JournalEntry/record/entries()/__len__ convention (identical across
all 12 prior MSI packages in this arc), never that legacy file's
schema or file-I/O mechanism.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .models import DecisionOutcomePair, DecisionRecord, OutcomeRecord
from .serialization import decision_record_to_dict, outcome_record_to_dict, pair_to_dict


@dataclass(frozen=True)
class JournalEntry:
    kind: str
    recorded_at: str
    payload: dict


class DecisionAuditorJournal:
    """Records BOTH `TRADE_APPROVED` and `NO_TRADE` decisions -- never
    discards a no-trade day, per Deliverable 3's explicit mandate."""

    def __init__(self) -> None:
        self._entries: List[JournalEntry] = []

    def record_decision(self, decision: DecisionRecord, *, recorded_at: str) -> None:
        self._entries.append(JournalEntry(kind="decision_recorded", recorded_at=recorded_at,
                                          payload=decision_record_to_dict(decision)))

    def record_outcome(self, outcome: OutcomeRecord, *, recorded_at: str) -> None:
        self._entries.append(JournalEntry(kind="outcome_recorded", recorded_at=recorded_at,
                                          payload=outcome_record_to_dict(outcome)))

    def record_pair(self, pair: DecisionOutcomePair, *, recorded_at: str) -> None:
        self._entries.append(JournalEntry(kind="pair_recorded", recorded_at=recorded_at,
                                          payload=pair_to_dict(pair)))

    def entries(self) -> Tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
