"""Position Lifecycle Intelligence runner — Series 96. Dual batch/
streaming entrypoints, proven byte-identical by test (house convention)."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from .engine import assess_position_lifecycle
from .journal import PositionLifecycleJournal
from .models import PositionLifecycleAssessment


def assess_lifecycles_batch(requests: Sequence[dict]) -> Tuple[PositionLifecycleAssessment, ...]:
    """Each request is a dict of assess_position_lifecycle's kwargs."""
    return tuple(assess_position_lifecycle(**req) for req in requests)


class PositionLifecycleStream:
    def __init__(self) -> None:
        self.journal = PositionLifecycleJournal()
        self._results: List[PositionLifecycleAssessment] = []

    def submit(self, **kwargs) -> PositionLifecycleAssessment:
        assessment = assess_position_lifecycle(**kwargs)
        self._results.append(assessment)
        self.journal.record_assessment(assessment, recorded_at=kwargs["timestamp"])
        return assessment

    def results(self) -> Tuple[PositionLifecycleAssessment, ...]:
        return tuple(self._results)
