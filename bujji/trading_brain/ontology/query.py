"""Read-only query layer over a collection of TradingOntologySnapshot
records. Mirrors the *Index pattern established throughout MIC v2:
ingest(), then read-only queries. No mutation method beyond ingest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import TradingOntologySnapshot


@dataclass
class OntologyIndex:
    _records: List[TradingOntologySnapshot] = field(default_factory=list)

    def ingest(self, snapshot: TradingOntologySnapshot) -> None:
        self._records.append(snapshot)

    def latest(self) -> Optional[TradingOntologySnapshot]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[TradingOntologySnapshot]:
        return list(self._records)

    def find_by_id(self, snapshot_id: str) -> Optional[TradingOntologySnapshot]:
        for r in self._records:
            if r.snapshot_id == snapshot_id:
                return r
        return None

    def find_by_market_state(self, market_state: str) -> List[TradingOntologySnapshot]:
        return [r for r in self._records if r.market_state == market_state]

    def find_by_execution_intent(self, execution_intent: str) -> List[TradingOntologySnapshot]:
        return [r for r in self._records if r.execution_intent == execution_intent]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.market_state] = counts.get(r.market_state, 0) + 1
        return counts
