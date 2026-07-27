"""Read-only query layer over a collection of RuntimeSession records.
Mirrors the *Index pattern established throughout MIC v2 and the
Trading Brain: ingest(), then read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import RuntimeSession


@dataclass
class RuntimeSessionIndex:
    _records: List[RuntimeSession] = field(default_factory=list)

    def ingest(self, session: RuntimeSession) -> None:
        self._records.append(session)

    def latest(self) -> Optional[RuntimeSession]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[RuntimeSession]:
        return list(self._records)

    def find_by_id(self, session_id: str) -> Optional[RuntimeSession]:
        for r in self._records:
            if r.session_id == session_id:
                return r
        return None

    def find_by_state(self, session_state: str) -> List[RuntimeSession]:
        return [r for r in self._records if r.session_state == session_state]

    def existing_session_ids(self) -> List[str]:
        return [r.session_id for r in self._records]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.session_state] = counts.get(r.session_state, 0) + 1
        return counts
