"""Pure, read-only query helpers over recorded
StrategyEligibilityAssessments. No mutation, no analytics beyond
simple lookup -- mirrors `bujji.msi_consensus.query`'s discipline
exactly.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .models import StrategyEligibilityAssessment


def assessment_by_id(assessments: Tuple[StrategyEligibilityAssessment, ...], assessment_id: str) -> Optional[StrategyEligibilityAssessment]:
    match: Optional[StrategyEligibilityAssessment] = None
    for a in assessments:
        if a.assessment_id == assessment_id:
            match = a
    return match


def assessments_by_confidence(assessments: Tuple[StrategyEligibilityAssessment, ...], eligibility_confidence: str) -> Tuple[StrategyEligibilityAssessment, ...]:
    return tuple(a for a in assessments if a.eligibility_confidence == eligibility_confidence)


def assessments_in_time_range(assessments: Tuple[StrategyEligibilityAssessment, ...], start_timestamp: str, end_timestamp: str) -> Tuple[StrategyEligibilityAssessment, ...]:
    return tuple(a for a in assessments if start_timestamp <= a.timestamp <= end_timestamp)


def assessments_with_family_eligible(assessments: Tuple[StrategyEligibilityAssessment, ...], family: str) -> Tuple[StrategyEligibilityAssessment, ...]:
    return tuple(a for a in assessments if family in a.eligible_strategy_families)


def assessments_with_contradictions(assessments: Tuple[StrategyEligibilityAssessment, ...]) -> Tuple[StrategyEligibilityAssessment, ...]:
    return tuple(a for a in assessments if a.contradictions)
