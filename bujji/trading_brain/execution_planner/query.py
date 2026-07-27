"""Read-only query layer over a collection of ExecutionPlan records.
Mirrors the *Index pattern established throughout MIC v2 and the
Trading Brain: ingest(), then read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import ExecutionPlan


@dataclass
class ExecutionPlanIndex:
    _records: List[ExecutionPlan] = field(default_factory=list)

    def ingest(self, plan: ExecutionPlan) -> None:
        self._records.append(plan)

    def latest(self) -> Optional[ExecutionPlan]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[ExecutionPlan]:
        return list(self._records)

    def find_by_id(self, plan_id: str) -> Optional[ExecutionPlan]:
        for r in self._records:
            if r.plan_id == plan_id:
                return r
        return None

    def find_by_status(self, status: str) -> List[ExecutionPlan]:
        return [r for r in self._records if r.status == status]

    def find_by_strategy(self, strategy_id: str) -> List[ExecutionPlan]:
        return [r for r in self._records if r.strategy_id == strategy_id]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.status] = counts.get(r.status, 0) + 1
        return counts
