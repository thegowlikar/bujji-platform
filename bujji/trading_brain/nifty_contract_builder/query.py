"""Read-only query layer over a collection of
ContractConstructionResult records. Mirrors the *Index pattern
established throughout MIC v2 and the Trading Brain: ingest(), then
read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import ContractConstructionResult


@dataclass
class ContractConstructionIndex:
    _records: List[ContractConstructionResult] = field(default_factory=list)

    def ingest(self, result: ContractConstructionResult) -> None:
        self._records.append(result)

    def latest(self) -> Optional[ContractConstructionResult]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[ContractConstructionResult]:
        return list(self._records)

    def find_by_id(self, construction_id: str) -> Optional[ContractConstructionResult]:
        for r in self._records:
            if r.construction_id == construction_id:
                return r
        return None

    def find_by_status(self, status: str) -> List[ContractConstructionResult]:
        return [r for r in self._records if r.status == status]

    def find_by_strategy(self, strategy_id: str) -> List[ContractConstructionResult]:
        return [r for r in self._records if r.strategy_id == strategy_id]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.status] = counts.get(r.status, 0) + 1
        return counts
