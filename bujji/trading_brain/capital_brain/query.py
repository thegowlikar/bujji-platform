"""Read-only query layer over a collection of CapitalDecision records.
Mirrors the *Index pattern established throughout MIC v2 and the
Trading Brain: ingest(), then read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import CapitalDecision


@dataclass
class CapitalDecisionIndex:
    _records: List[CapitalDecision] = field(default_factory=list)

    def ingest(self, decision: CapitalDecision) -> None:
        self._records.append(decision)

    def latest(self) -> Optional[CapitalDecision]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[CapitalDecision]:
        return list(self._records)

    def find_by_id(self, decision_id: str) -> Optional[CapitalDecision]:
        for r in self._records:
            if r.decision_id == decision_id:
                return r
        return None

    def find_by_capital_intent(self, capital_intent: str) -> List[CapitalDecision]:
        return [r for r in self._records if r.capital_intent == capital_intent]

    def find_by_allocation_status(self, allocation_status: str) -> List[CapitalDecision]:
        return [r for r in self._records if r.allocation_status == allocation_status]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.capital_intent] = counts.get(r.capital_intent, 0) + 1
        return counts
