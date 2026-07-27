"""Read-only query layer over a collection of MarketStateAssessment
records. Mirrors the *Index pattern established throughout MIC v2, the
Trading Ontology, and the Evidence Interpreter: ingest(), then
read-only queries only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import MarketStateAssessment


@dataclass
class MarketStateIndex:
    _records: List[MarketStateAssessment] = field(default_factory=list)

    def ingest(self, assessment: MarketStateAssessment) -> None:
        self._records.append(assessment)

    def latest(self) -> Optional[MarketStateAssessment]:
        if not self._records:
            return None
        return self._records[-1]

    def history(self) -> List[MarketStateAssessment]:
        return list(self._records)

    def find_by_id(self, assessment_id: str) -> Optional[MarketStateAssessment]:
        for r in self._records:
            if r.assessment_id == assessment_id:
                return r
        return None

    def find_by_market_state(self, market_state: str) -> List[MarketStateAssessment]:
        return [r for r in self._records if r.market_state == market_state]

    def find_by_market_character(self, market_character: str) -> List[MarketStateAssessment]:
        return [r for r in self._records if r.market_character == market_character]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.market_character] = counts.get(r.market_character, 0) + 1
        return counts
