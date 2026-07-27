"""Read-only query layer over a collection of BrokerExecutionRequest
records. Mirrors the *Index pattern established throughout MIC v2 and
the Trading Brain: ingest(), then read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import BrokerExecutionRequest


@dataclass
class BrokerExecutionRequestIndex:
    _records: List[BrokerExecutionRequest] = field(default_factory=list)

    def ingest(self, request: BrokerExecutionRequest) -> None:
        self._records.append(request)

    def latest(self) -> Optional[BrokerExecutionRequest]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[BrokerExecutionRequest]:
        return list(self._records)

    def find_by_id(self, request_id: str) -> Optional[BrokerExecutionRequest]:
        for r in self._records:
            if r.request_id == request_id:
                return r
        return None

    def find_by_broker(self, broker: str) -> List[BrokerExecutionRequest]:
        return [r for r in self._records if r.broker == broker]

    def find_by_status(self, execution_status: str) -> List[BrokerExecutionRequest]:
        return [r for r in self._records if r.execution_status == execution_status]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.execution_status] = counts.get(r.execution_status, 0) + 1
        return counts
