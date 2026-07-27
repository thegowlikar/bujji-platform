"""Read-only query helpers over a sequence of
MarketParticipantPositioningAssessment records. No analytics/aggregation
beyond simple lookup."""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from .models import MarketParticipantPositioningAssessment


def by_id(assessments: Sequence[MarketParticipantPositioningAssessment], assessment_id: str) -> Optional[MarketParticipantPositioningAssessment]:
    for a in assessments:
        if a.assessment_id == assessment_id:
            return a
    return None


def by_bias(assessments: Sequence[MarketParticipantPositioningAssessment], bias: str) -> Tuple[MarketParticipantPositioningAssessment, ...]:
    return tuple(a for a in assessments if a.positioning_bias == bias)


def in_time_range(assessments: Sequence[MarketParticipantPositioningAssessment], start_ts: str, end_ts: str) -> Tuple[MarketParticipantPositioningAssessment, ...]:
    return tuple(a for a in assessments if start_ts <= a.timestamp <= end_ts)


def latest(assessments: Sequence[MarketParticipantPositioningAssessment]) -> Optional[MarketParticipantPositioningAssessment]:
    if not assessments:
        return None
    return max(assessments, key=lambda a: a.timestamp)
