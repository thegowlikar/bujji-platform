"""Read-only query layer over a collection of ExecutionSession
records. Mirrors the *Index pattern established throughout MIC v2 and
the Trading Brain: ingest(), then read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import ExecutionSession


@dataclass
class ExecutionSessionIndex:
    _records: List[ExecutionSession] = field(default_factory=list)

    def ingest(self, session: ExecutionSession) -> None:
        self._records.append(session)

    def latest(self) -> Optional[ExecutionSession]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[ExecutionSession]:
        return list(self._records)

    def find_by_id(self, session_id: str) -> Optional[ExecutionSession]:
        for r in self._records:
            if r.session_id == session_id:
                return r
        return None

    def find_by_state(self, execution_state: str) -> List[ExecutionSession]:
        return [r for r in self._records if r.execution_state == execution_state]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.execution_state] = counts.get(r.execution_state, 0) + 1
        return counts
