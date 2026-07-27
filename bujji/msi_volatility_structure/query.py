"""Read-only query helpers over a sequence of VolatilityStructureAssessment
records. No analytics/aggregation beyond simple lookup."""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from .models import VolatilityStructureAssessment


def by_id(assessments: Sequence[VolatilityStructureAssessment], assessment_id: str) -> Optional[VolatilityStructureAssessment]:
    for a in assessments:
        if a.assessment_id == assessment_id:
            return a
    return None


def by_regime(assessments: Sequence[VolatilityStructureAssessment], regime: str) -> Tuple[VolatilityStructureAssessment, ...]:
    return tuple(a for a in assessments if a.volatility_regime == regime)


def latest(assessments: Sequence[VolatilityStructureAssessment]) -> Optional[VolatilityStructureAssessment]:
    if not assessments:
        return None
    return max(assessments, key=lambda a: a.timestamp)
