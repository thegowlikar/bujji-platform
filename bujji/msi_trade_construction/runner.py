"""Trade Construction Foundation runner — Series 90. Dual batch/streaming
entrypoints, proven byte-identical by test (house convention since
Series 78)."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .engine import construct_trade
from .journal import TradeConstructionJournal
from .models import TradeConstructionAssessment


def construct_trades_batch(
    requests: Sequence[dict],
) -> Tuple[TradeConstructionAssessment, ...]:
    """Each request is a dict of construct_trade's kwargs (strategy_family,
    chain, spot, as_of_date, direction, expected_move_pct,
    supporting_assessment_ids, timestamp, min_dte, max_dte)."""
    return tuple(construct_trade(**req) for req in requests)


class TradeConstructionStream:
    """Streaming entrypoint: same per-request logic as the batch runner,
    called one request at a time, journaling each result -- proven to
    produce byte-identical assessments to `construct_trades_batch` for
    the same input sequence."""

    def __init__(self) -> None:
        self.journal = TradeConstructionJournal()
        self._results: List[TradeConstructionAssessment] = []

    def submit(self, **kwargs) -> TradeConstructionAssessment:
        assessment = construct_trade(**kwargs)
        self._results.append(assessment)
        self.journal.record_assessment(assessment, recorded_at=kwargs["timestamp"])
        return assessment

    def results(self) -> Tuple[TradeConstructionAssessment, ...]:
        return tuple(self._results)
