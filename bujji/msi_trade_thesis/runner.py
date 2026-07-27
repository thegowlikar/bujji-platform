"""Trade Thesis Engine runner — Series 92. Dual batch/streaming
entrypoints, proven byte-identical by test (house convention)."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from .engine import derive_trade_thesis
from .journal import TradeThesisJournal
from .models import TradeThesisAssessment


def derive_theses_batch(requests: Sequence[dict]) -> Tuple[TradeThesisAssessment, ...]:
    """Each request is a dict of derive_trade_thesis's kwargs
    (psi, mssi, mdi, mppi, vsb, consensus, timestamp)."""
    return tuple(derive_trade_thesis(**req) for req in requests)


class TradeThesisStream:
    def __init__(self) -> None:
        self.journal = TradeThesisJournal()
        self._results: List[TradeThesisAssessment] = []

    def submit(self, **kwargs) -> TradeThesisAssessment:
        assessment = derive_trade_thesis(**kwargs)
        self._results.append(assessment)
        self.journal.record_assessment(assessment, recorded_at=kwargs["timestamp"])
        return assessment

    def results(self) -> Tuple[TradeThesisAssessment, ...]:
        return tuple(self._results)
