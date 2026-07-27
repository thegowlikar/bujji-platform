"""Pure, read-only query helpers over recorded MarketOpportunityAssessments.
No mutation, no analytics beyond simple lookup -- mirrors
`bujji.market_episode.query`'s discipline.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .models import MarketOpportunityAssessment


def assessment_by_id(
    assessments: Tuple[MarketOpportunityAssessment, ...], assessment_id: str
) -> Optional[MarketOpportunityAssessment]:
    for a in assessments:
        if a.assessment_id == assessment_id:
            return a
    return None


def assessments_by_opportunity_state(
    assessments: Tuple[MarketOpportunityAssessment, ...], opportunity_state: str
) -> Tuple[MarketOpportunityAssessment, ...]:
    return tuple(a for a in assessments if a.opportunity_state == opportunity_state)


def assessments_in_time_range(
    assessments: Tuple[MarketOpportunityAssessment, ...], start_timestamp: str, end_timestamp: str
) -> Tuple[MarketOpportunityAssessment, ...]:
    return tuple(a for a in assessments if start_timestamp <= a.timestamp <= end_timestamp)


def assessments_for_episode(
    assessments: Tuple[MarketOpportunityAssessment, ...], episode_id: str
) -> Tuple[MarketOpportunityAssessment, ...]:
    return tuple(a for a in assessments if episode_id in a.episode_ids)


def latest_assessment(
    assessments: Tuple[MarketOpportunityAssessment, ...]
) -> Optional[MarketOpportunityAssessment]:
    """Latest by `timestamp` (ISO-8601 string, lexicographically
    comparable); ties broken by last-in-sequence (append order),
    matching `bujji.market_episode.query.episode_by_id`'s "last one
    found wins" convention."""
    if not assessments:
        return None
    return max(enumerate(assessments), key=lambda pair: (pair[1].timestamp, pair[0]))[1]
