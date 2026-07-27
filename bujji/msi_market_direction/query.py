"""Read-only query helpers over a sequence of MarketDirectionAssessment
records. No analytics/aggregation beyond simple lookup."""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from .models import MarketDirectionAssessment


def by_id(assessments: Sequence[MarketDirectionAssessment], assessment_id: str) -> Optional[MarketDirectionAssessment]:
    for a in assessments:
        if a.assessment_id == assessment_id:
            return a
    return None


def by_direction(assessments: Sequence[MarketDirectionAssessment], direction: str) -> Tuple[MarketDirectionAssessment, ...]:
    return tuple(a for a in assessments if a.overall_direction == direction)


def in_time_range(assessments: Sequence[MarketDirectionAssessment], start_ts: str, end_ts: str) -> Tuple[MarketDirectionAssessment, ...]:
    return tuple(a for a in assessments if start_ts <= a.timestamp <= end_ts)


def latest(assessments: Sequence[MarketDirectionAssessment]) -> Optional[MarketDirectionAssessment]:
    if not assessments:
        return None
    return max(assessments, key=lambda a: a.timestamp)
