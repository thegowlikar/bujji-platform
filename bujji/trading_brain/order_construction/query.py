"""Read-only query layer over a collection of OrderConstructionResult
records. Mirrors the *Index pattern established throughout MIC v2 and
the Trading Brain: ingest(), then read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import OrderConstructionResult


@dataclass
class OrderConstructionIndex:
    _records: List[OrderConstructionResult] = field(default_factory=list)

    def ingest(self, result: OrderConstructionResult) -> None:
        self._records.append(result)

    def latest(self) -> Optional[OrderConstructionResult]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[OrderConstructionResult]:
        return list(self._records)

    def find_by_id(self, construction_id: str) -> Optional[OrderConstructionResult]:
        for r in self._records:
            if r.construction_id == construction_id:
                return r
        return None

    def find_by_status(self, status: str) -> List[OrderConstructionResult]:
        return [r for r in self._records if r.status == status]

    def find_by_position_plan(self, position_plan_id: str) -> List[OrderConstructionResult]:
        return [r for r in self._records if r.position_plan_id == position_plan_id]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.status] = counts.get(r.status, 0) + 1
        return counts
