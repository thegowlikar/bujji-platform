"""Read-only query layer over a collection of PositionPlan records.
Mirrors the *Index pattern established throughout MIC v2 and the
Trading Brain: ingest(), then read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import PositionPlan


@dataclass
class PositionPlanIndex:
    _records: List[PositionPlan] = field(default_factory=list)

    def ingest(self, plan: PositionPlan) -> None:
        self._records.append(plan)

    def latest(self) -> Optional[PositionPlan]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[PositionPlan]:
        return list(self._records)

    def find_by_id(self, plan_id: str) -> Optional[PositionPlan]:
        for r in self._records:
            if r.plan_id == plan_id:
                return r
        return None

    def find_by_validation(self, validation: str) -> List[PositionPlan]:
        return [r for r in self._records if r.validation == validation]

    def find_by_capital_intent(self, capital_intent: str) -> List[PositionPlan]:
        return [r for r in self._records if r.capital_intent == capital_intent]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.validation] = counts.get(r.validation, 0) + 1
        return counts
