"""Read-only query layer over a collection of EvidenceInterpretation
records. Mirrors the *Index pattern established throughout MIC v2 and
the Trading Ontology: ingest(), then read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import EvidenceInterpretation


@dataclass
class EvidenceInterpretationIndex:
    _records: List[EvidenceInterpretation] = field(default_factory=list)

    def ingest(self, interpretation: EvidenceInterpretation) -> None:
        self._records.append(interpretation)

    def latest(self) -> Optional[EvidenceInterpretation]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[EvidenceInterpretation]:
        return list(self._records)

    def find_by_id(self, interpretation_id: str) -> Optional[EvidenceInterpretation]:
        for r in self._records:
            if r.interpretation_id == interpretation_id:
                return r
        return None

    def find_by_market_state(self, market_state: str) -> List[EvidenceInterpretation]:
        return [r for r in self._records if r.ontology_snapshot.market_state == market_state]

    def find_by_risk_state(self, risk_state: str) -> List[EvidenceInterpretation]:
        return [r for r in self._records if r.ontology_snapshot.risk_state == risk_state]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            key = r.ontology_snapshot.market_state
            counts[key] = counts.get(key, 0) + 1
        return counts
