"""Pure, read-only query helpers over recorded ConsensusAssessments.
No mutation, no analytics beyond simple lookup — mirrors
`bujji.msi_market_structure.query`'s discipline exactly.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .models import ConsensusAssessment


def assessment_by_id(assessments: Tuple[ConsensusAssessment, ...], assessment_id: str) -> Optional[ConsensusAssessment]:
    match: Optional[ConsensusAssessment] = None
    for a in assessments:
        if a.assessment_id == assessment_id:
            match = a
    return match


def assessments_by_consensus_level(assessments: Tuple[ConsensusAssessment, ...], consensus_level: str) -> Tuple[ConsensusAssessment, ...]:
    return tuple(a for a in assessments if a.consensus_level == consensus_level)


def assessments_in_time_range(assessments: Tuple[ConsensusAssessment, ...], start_timestamp: str, end_timestamp: str) -> Tuple[ConsensusAssessment, ...]:
    return tuple(a for a in assessments if start_timestamp <= a.timestamp <= end_timestamp)


def assessments_for_domain(assessments: Tuple[ConsensusAssessment, ...], domain_name: str) -> Tuple[ConsensusAssessment, ...]:
    return tuple(a for a in assessments if domain_name in a.participating_domains)


def assessments_with_conflicts(assessments: Tuple[ConsensusAssessment, ...]) -> Tuple[ConsensusAssessment, ...]:
    return tuple(a for a in assessments if a.conflicting_domains)
