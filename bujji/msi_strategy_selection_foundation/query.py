"""Read-only query helpers over a sequence of StrategySuitabilityAssessment
records. No analytics/aggregation/ranking beyond simple lookup."""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from .models import StrategySuitabilityAssessment


def by_id(assessments: Sequence[StrategySuitabilityAssessment], assessment_id: str) -> Optional[StrategySuitabilityAssessment]:
    for a in assessments:
        if a.assessment_id == assessment_id:
            return a
    return None


def by_family(assessments: Sequence[StrategySuitabilityAssessment], family: str) -> Optional[StrategySuitabilityAssessment]:
    for a in assessments:
        if a.strategy_family == family:
            return a
    return None


def by_suitability(assessments: Sequence[StrategySuitabilityAssessment], suitability: str) -> Tuple[StrategySuitabilityAssessment, ...]:
    return tuple(a for a in assessments if a.suitability == suitability)
