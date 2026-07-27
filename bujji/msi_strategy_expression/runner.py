"""Strategy Expression Engine runner — Series 93. Dual batch/streaming
entrypoints, proven byte-identical by test (house convention)."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from .engine import derive_strategy_expression
from .journal import StrategyExpressionJournal
from .models import StrategyExpressionAssessment


def derive_expressions_batch(requests: Sequence[dict]) -> Tuple[StrategyExpressionAssessment, ...]:
    """Each request is a dict of derive_strategy_expression's kwargs (thesis, timestamp)."""
    return tuple(derive_strategy_expression(**req) for req in requests)


class StrategyExpressionStream:
    def __init__(self) -> None:
        self.journal = StrategyExpressionJournal()
        self._results: List[StrategyExpressionAssessment] = []

    def submit(self, **kwargs) -> StrategyExpressionAssessment:
        assessment = derive_strategy_expression(**kwargs)
        self._results.append(assessment)
        self.journal.record_assessment(assessment, recorded_at=kwargs["timestamp"])
        return assessment

    def results(self) -> Tuple[StrategyExpressionAssessment, ...]:
        return tuple(self._results)
