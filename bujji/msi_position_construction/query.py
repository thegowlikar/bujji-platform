"""Position Construction Intelligence query helpers — Series 95. Pure,
read-only lookups, mirroring every prior MSI package's query.py
convention."""
from __future__ import annotations

from typing import Optional, Sequence

from .models import PositionConstructionAssessment


def by_id(assessments: Sequence[PositionConstructionAssessment], assessment_id: str) -> Optional[PositionConstructionAssessment]:
    for a in assessments:
        if a.assessment_id == assessment_id:
            return a
    return None


def by_construction_type(assessments: Sequence[PositionConstructionAssessment], construction_type: str) -> tuple:
    return tuple(a for a in assessments if a.construction_type == construction_type)


def by_family(assessments: Sequence[PositionConstructionAssessment], family: str) -> tuple:
    return tuple(a for a in assessments if a.selected_strategy_family == family)


def latest(assessments: Sequence[PositionConstructionAssessment]) -> Optional[PositionConstructionAssessment]:
    if not assessments:
        return None
    return max(assessments, key=lambda a: a.timestamp)
