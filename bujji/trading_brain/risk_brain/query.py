"""Read-only query layer over a collection of RiskAssessment records.
Mirrors the *Index pattern established throughout MIC v2 and the
Trading Brain: ingest(), then read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import RiskAssessment


@dataclass
class RiskAssessmentIndex:
    _records: List[RiskAssessment] = field(default_factory=list)

    def ingest(self, assessment: RiskAssessment) -> None:
        self._records.append(assessment)

    def latest(self) -> Optional[RiskAssessment]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[RiskAssessment]:
        return list(self._records)

    def find_by_id(self, assessment_id: str) -> Optional[RiskAssessment]:
        for r in self._records:
            if r.assessment_id == assessment_id:
                return r
        return None

    def find_by_status(self, status: str) -> List[RiskAssessment]:
        return [r for r in self._records if r.status == status]

    def find_by_approval(self, approval: str) -> List[RiskAssessment]:
        return [r for r in self._records if r.approval == approval]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.status] = counts.get(r.status, 0) + 1
        return counts
