"""Read-only query layer over a collection of
DecisionPipelineQualification records. Mirrors the *Index pattern
established throughout MIC v2 and the Trading Brain: ingest(), then
read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import DecisionPipelineQualification


@dataclass
class QualificationIndex:
    _records: List[DecisionPipelineQualification] = field(default_factory=list)

    def ingest(self, qualification: DecisionPipelineQualification) -> None:
        self._records.append(qualification)

    def latest(self) -> Optional[DecisionPipelineQualification]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[DecisionPipelineQualification]:
        return list(self._records)

    def find_by_id(self, qualification_id: str) -> Optional[DecisionPipelineQualification]:
        for r in self._records:
            if r.qualification_id == qualification_id:
                return r
        return None

    def find_by_status(self, pipeline_status: str) -> List[DecisionPipelineQualification]:
        return [r for r in self._records if r.pipeline_status == pipeline_status]

    def find_by_fingerprint(self, fingerprint: str) -> List[DecisionPipelineQualification]:
        return [r for r in self._records if r.decision_fingerprint == fingerprint]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.pipeline_status] = counts.get(r.pipeline_status, 0) + 1
        return counts
