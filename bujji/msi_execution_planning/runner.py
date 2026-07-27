"""Execution Planning Engine runner — Series 98. Dual batch/streaming
entrypoints, proven byte-identical by test (house convention)."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from .engine import build_execution_plan
from .journal import ExecutionPlanningJournal
from .models import ExecutionPlanAssessment


def build_execution_plans_batch(requests: Sequence[dict]) -> Tuple[ExecutionPlanAssessment, ...]:
    """Each request is a dict of build_execution_plan's kwargs."""
    return tuple(build_execution_plan(**req) for req in requests)


class ExecutionPlanningStream:
    def __init__(self) -> None:
        self.journal = ExecutionPlanningJournal()
        self._results: List[ExecutionPlanAssessment] = []

    def submit(self, **kwargs) -> ExecutionPlanAssessment:
        assessment = build_execution_plan(**kwargs)
        self._results.append(assessment)
        self.journal.record_assessment(assessment, recorded_at=kwargs["timestamp"])
        return assessment

    def results(self) -> Tuple[ExecutionPlanAssessment, ...]:
        return tuple(self._results)
