"""Pure, read-only query helpers over recorded MarketStructureAssessments.
No mutation, no analytics beyond simple lookup — mirrors
`bujji.msi_price_structure.query`'s discipline exactly.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .models import MarketStructureAssessment


def assessment_by_id(assessments: Tuple[MarketStructureAssessment, ...], assessment_id: str) -> Optional[MarketStructureAssessment]:
    match: Optional[MarketStructureAssessment] = None
    for a in assessments:
        if a.assessment_id == assessment_id:
            match = a
    return match


def assessments_by_structure_location(assessments: Tuple[MarketStructureAssessment, ...], structure_location: str) -> Tuple[MarketStructureAssessment, ...]:
    return tuple(a for a in assessments if a.structure_location == structure_location)


def assessments_in_time_range(assessments: Tuple[MarketStructureAssessment, ...], start_timestamp: str, end_timestamp: str) -> Tuple[MarketStructureAssessment, ...]:
    return tuple(a for a in assessments if start_timestamp <= a.timestamp <= end_timestamp)


def assessments_for_episode(assessments: Tuple[MarketStructureAssessment, ...], episode_id: str) -> Tuple[MarketStructureAssessment, ...]:
    return tuple(a for a in assessments if episode_id in a.supporting_episode_ids)
