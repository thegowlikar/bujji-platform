"""Execution Planning Engine query helpers — Series 98. Pure, read-only
lookups, mirroring every prior MSI package's query.py convention."""
from __future__ import annotations

from typing import Optional, Sequence

from .models import ExecutionPlanAssessment


def by_id(assessments: Sequence[ExecutionPlanAssessment], plan_id: str) -> Optional[ExecutionPlanAssessment]:
    for a in assessments:
        if a.plan_id == plan_id:
            return a
    return None


def by_strategy_family(assessments: Sequence[ExecutionPlanAssessment], family: str) -> tuple:
    return tuple(a for a in assessments if a.strategy_family == family)


def latest(assessments: Sequence[ExecutionPlanAssessment]) -> Optional[ExecutionPlanAssessment]:
    if not assessments:
        return None
    return max(assessments, key=lambda a: a.timestamp)
