"""Read-only query helpers over a sequence of StrategySelectionAssessment
records. No analytics/ranking beyond simple lookup."""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from .models import StrategySelectionAssessment


def by_id(assessments: Sequence[StrategySelectionAssessment], assessment_id: str) -> Optional[StrategySelectionAssessment]:
    for a in assessments:
        if a.assessment_id == assessment_id:
            return a
    return None


def by_selected_family(assessments: Sequence[StrategySelectionAssessment], family: Optional[str]) -> Tuple[StrategySelectionAssessment, ...]:
    return tuple(a for a in assessments if a.selected_strategy_family == family)


def latest(assessments: Sequence[StrategySelectionAssessment]) -> Optional[StrategySelectionAssessment]:
    if not assessments:
        return None
    return max(assessments, key=lambda a: a.timestamp)
