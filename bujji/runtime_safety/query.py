"""Read-only query layer over a collection of RuntimeAuthorization
records. Mirrors the *Index pattern established throughout MIC v2 and
the Trading Brain: ingest(), then read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import RuntimeAuthorization


@dataclass
class RuntimeAuthorizationIndex:
    _records: List[RuntimeAuthorization] = field(default_factory=list)

    def ingest(self, authorization: RuntimeAuthorization) -> None:
        self._records.append(authorization)

    def latest(self) -> Optional[RuntimeAuthorization]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[RuntimeAuthorization]:
        return list(self._records)

    def find_by_id(self, authorization_id: str) -> Optional[RuntimeAuthorization]:
        for r in self._records:
            if r.authorization_id == authorization_id:
                return r
        return None

    def find_by_state(self, authorization_state: str) -> List[RuntimeAuthorization]:
        return [r for r in self._records if r.authorization_state == authorization_state]

    def find_by_session(self, execution_session_id: str) -> List[RuntimeAuthorization]:
        return [r for r in self._records if r.execution_session_id == execution_session_id]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.authorization_state] = counts.get(r.authorization_state, 0) + 1
        return counts
