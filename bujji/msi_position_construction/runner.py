"""Position Construction Intelligence runner — Series 95. Dual batch/
streaming entrypoints, proven byte-identical by test (house convention)."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from .engine import construct_position
from .journal import PositionConstructionJournal
from .models import PositionConstructionAssessment


def construct_positions_batch(requests: Sequence[dict]) -> Tuple[PositionConstructionAssessment, ...]:
    """Each request is a dict of construct_position's kwargs (selection, expression, thesis, timestamp)."""
    return tuple(construct_position(**req) for req in requests)


class PositionConstructionStream:
    def __init__(self) -> None:
        self.journal = PositionConstructionJournal()
        self._results: List[PositionConstructionAssessment] = []

    def submit(self, **kwargs) -> PositionConstructionAssessment:
        assessment = construct_position(**kwargs)
        self._results.append(assessment)
        self.journal.record_assessment(assessment, recorded_at=kwargs["timestamp"])
        return assessment

    def results(self) -> Tuple[PositionConstructionAssessment, ...]:
        return tuple(self._results)
