"""Strategy Expression Engine query helpers — Series 93. Pure,
read-only lookups, mirroring every prior MSI package's query.py
convention."""
from __future__ import annotations

from typing import Optional, Sequence

from .models import StrategyExpressionAssessment


def by_id(assessments: Sequence[StrategyExpressionAssessment], assessment_id: str) -> Optional[StrategyExpressionAssessment]:
    for a in assessments:
        if a.assessment_id == assessment_id:
            return a
    return None


def by_compatible_family(assessments: Sequence[StrategyExpressionAssessment], family: str) -> tuple:
    return tuple(a for a in assessments if family in a.compatible_strategy_families)


def latest(assessments: Sequence[StrategyExpressionAssessment]) -> Optional[StrategyExpressionAssessment]:
    if not assessments:
        return None
    return max(assessments, key=lambda a: a.timestamp)
