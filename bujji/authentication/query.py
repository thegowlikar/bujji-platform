"""Read-only query layer over a collection of BrokerSession records.
Mirrors the *Index pattern established throughout MIC v2 and the
Trading Brain: ingest(), then read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import BrokerSession


@dataclass
class BrokerSessionIndex:
    _records: List[BrokerSession] = field(default_factory=list)

    def ingest(self, session: BrokerSession) -> None:
        self._records.append(session)

    def latest(self) -> Optional[BrokerSession]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[BrokerSession]:
        return list(self._records)

    def find_by_id(self, broker_session_id: str) -> Optional[BrokerSession]:
        for r in self._records:
            if r.broker_session_id == broker_session_id:
                return r
        return None

    def find_by_authentication_state(self, authentication_state: str) -> List[BrokerSession]:
        return [r for r in self._records if r.authentication_state == authentication_state]

    def find_by_session_state(self, session_state: str) -> List[BrokerSession]:
        return [r for r in self._records if r.session_state == session_state]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.authentication_state] = counts.get(r.authentication_state, 0) + 1
        return counts
