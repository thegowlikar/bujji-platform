"""Margin Bridge & Capital Fidelity runner — Series 97. Dual batch/
streaming entrypoints, proven byte-identical by test (house convention)."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from .engine import estimate_margin
from .journal import MarginBridgeJournal
from .models import MarginEstimate


def estimate_margins_batch(requests: Sequence[dict]) -> Tuple[MarginEstimate, ...]:
    """Each request is a dict of estimate_margin's kwargs."""
    return tuple(estimate_margin(**req) for req in requests)


class MarginBridgeStream:
    def __init__(self) -> None:
        self.journal = MarginBridgeJournal()
        self._results: List[MarginEstimate] = []

    def submit(self, **kwargs) -> MarginEstimate:
        assessment = estimate_margin(**kwargs)
        self._results.append(assessment)
        self.journal.record_assessment(assessment, recorded_at=kwargs["timestamp"])
        return assessment

    def results(self) -> Tuple[MarginEstimate, ...]:
        return tuple(self._results)
