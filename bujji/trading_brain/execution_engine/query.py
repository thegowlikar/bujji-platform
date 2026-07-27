"""Read-only query layer over a collection of ExecutionInstructionSet
records. Mirrors the *Index pattern established throughout MIC v2 and
the Trading Brain: ingest(), then read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import ExecutionInstructionSet


@dataclass
class ExecutionInstructionSetIndex:
    _records: List[ExecutionInstructionSet] = field(default_factory=list)

    def ingest(self, instruction_set: ExecutionInstructionSet) -> None:
        self._records.append(instruction_set)

    def latest(self) -> Optional[ExecutionInstructionSet]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[ExecutionInstructionSet]:
        return list(self._records)

    def find_by_id(self, instruction_set_id: str) -> Optional[ExecutionInstructionSet]:
        for r in self._records:
            if r.instruction_set_id == instruction_set_id:
                return r
        return None

    def find_by_status(self, status: str) -> List[ExecutionInstructionSet]:
        return [r for r in self._records if r.status == status]

    def find_by_plan(self, plan_id: str) -> List[ExecutionInstructionSet]:
        return [r for r in self._records if r.plan_id == plan_id]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.status] = counts.get(r.status, 0) + 1
        return counts
