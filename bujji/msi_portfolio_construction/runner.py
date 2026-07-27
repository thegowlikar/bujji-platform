"""Portfolio & Risk Construction runner — Series 91. Dual batch/streaming
entrypoints, proven byte-identical by test (house convention)."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from .engine import evaluate_trade
from .journal import PortfolioConstructionJournal
from .models import PortfolioConstructionAssessment


def evaluate_trades_batch(requests: Sequence[dict]) -> Tuple[PortfolioConstructionAssessment, ...]:
    """Each request is a dict of evaluate_trade's kwargs."""
    return tuple(evaluate_trade(**req) for req in requests)


class PortfolioConstructionStream:
    def __init__(self) -> None:
        self.journal = PortfolioConstructionJournal()
        self._results: List[PortfolioConstructionAssessment] = []

    def submit(self, **kwargs) -> PortfolioConstructionAssessment:
        assessment = evaluate_trade(**kwargs)
        self._results.append(assessment)
        self.journal.record_assessment(assessment, recorded_at=kwargs["timestamp"])
        return assessment

    def results(self) -> Tuple[PortfolioConstructionAssessment, ...]:
        return tuple(self._results)
