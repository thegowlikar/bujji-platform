"""PerformanceAnalyticsJournal — append-only audit trail, mirroring
every prior MSI package's journal convention."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .models import CounterfactualReport, EdgeValidationReport, TradeAnalytics
from .serialization import counterfactual_report_to_dict, edge_report_to_dict, trade_analytics_to_dict


@dataclass(frozen=True)
class JournalEntry:
    kind: str
    recorded_at: str
    payload: dict


class PerformanceAnalyticsJournal:
    def __init__(self) -> None:
        self._entries: List[JournalEntry] = []

    def record_trade_analytics(self, t: TradeAnalytics, *, recorded_at: str) -> None:
        self._entries.append(JournalEntry(kind="trade_analytics_recorded", recorded_at=recorded_at,
                                          payload=trade_analytics_to_dict(t)))

    def record_edge_report(self, r: EdgeValidationReport, *, recorded_at: str) -> None:
        self._entries.append(JournalEntry(kind="edge_report_recorded", recorded_at=recorded_at,
                                          payload=edge_report_to_dict(r)))

    def record_counterfactual_report(self, r: CounterfactualReport, *, recorded_at: str) -> None:
        self._entries.append(JournalEntry(kind="counterfactual_report_recorded", recorded_at=recorded_at,
                                          payload=counterfactual_report_to_dict(r)))

    def entries(self) -> Tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
