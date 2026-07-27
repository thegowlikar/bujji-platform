"""Pure, read-only query helpers over recorded PriceStructureAssessments.
No mutation, no analytics beyond simple lookup — mirrors
`bujji.market_episode.query`'s discipline.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .models import PriceStructureAssessment


def assessment_by_id(assessments: Tuple[PriceStructureAssessment, ...], assessment_id: str) -> Optional[PriceStructureAssessment]:
    match: Optional[PriceStructureAssessment] = None
    for a in assessments:
        if a.assessment_id == assessment_id:
            match = a
    return match


def assessments_by_structure_state(assessments: Tuple[PriceStructureAssessment, ...], structure_state: str) -> Tuple[PriceStructureAssessment, ...]:
    return tuple(a for a in assessments if a.structure_state == structure_state)


def assessments_in_time_range(assessments: Tuple[PriceStructureAssessment, ...], start_timestamp: str, end_timestamp: str) -> Tuple[PriceStructureAssessment, ...]:
    return tuple(a for a in assessments if start_timestamp <= a.timestamp <= end_timestamp)


def assessments_for_episode(assessments: Tuple[PriceStructureAssessment, ...], episode_id: str) -> Tuple[PriceStructureAssessment, ...]:
    return tuple(a for a in assessments if episode_id in a.supporting_episode_ids)
