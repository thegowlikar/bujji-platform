"""Read-only query layer over a collection of StrategyDecision records.
Mirrors the *Index pattern established throughout MIC v2 and the
Trading Brain: ingest(), then read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import StrategyDecision


@dataclass
class StrategyDecisionIndex:
    _records: List[StrategyDecision] = field(default_factory=list)

    def ingest(self, decision: StrategyDecision) -> None:
        self._records.append(decision)

    def latest(self) -> Optional[StrategyDecision]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[StrategyDecision]:
        return list(self._records)

    def find_by_id(self, decision_id: str) -> Optional[StrategyDecision]:
        for r in self._records:
            if r.decision_id == decision_id:
                return r
        return None

    def find_by_selected_strategy(self, strategy_id: str) -> List[StrategyDecision]:
        return [r for r in self._records if r.selected_strategy == strategy_id]

    def find_by_status(self, selection_status: str) -> List[StrategyDecision]:
        return [r for r in self._records if r.selection_status == selection_status]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            key = r.selected_strategy or r.selection_status
            counts[key] = counts.get(key, 0) + 1
        return counts
