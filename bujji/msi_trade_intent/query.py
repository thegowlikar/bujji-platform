"""Pure, read-only query helpers over recorded TradeIntentAssessments.
No mutation, no analytics beyond simple lookup -- mirrors
`bujji.msi_strategy_eligibility.query`'s discipline exactly.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .models import TradeIntentAssessment


def assessment_by_id(assessments: Tuple[TradeIntentAssessment, ...], assessment_id: str) -> Optional[TradeIntentAssessment]:
    match: Optional[TradeIntentAssessment] = None
    for a in assessments:
        if a.assessment_id == assessment_id:
            match = a
    return match


def assessments_by_intent_state(assessments: Tuple[TradeIntentAssessment, ...], intent_state: str) -> Tuple[TradeIntentAssessment, ...]:
    return tuple(a for a in assessments if a.intent_state == intent_state)


def assessments_in_time_range(assessments: Tuple[TradeIntentAssessment, ...], start_timestamp: str, end_timestamp: str) -> Tuple[TradeIntentAssessment, ...]:
    return tuple(a for a in assessments if start_timestamp <= a.timestamp <= end_timestamp)


def assessments_with_family(assessments: Tuple[TradeIntentAssessment, ...], family: str) -> Tuple[TradeIntentAssessment, ...]:
    return tuple(a for a in assessments if a.selected_strategy_family == family)


def assessments_with_risk_profile(assessments: Tuple[TradeIntentAssessment, ...], risk_profile: str) -> Tuple[TradeIntentAssessment, ...]:
    return tuple(a for a in assessments if a.risk_profile == risk_profile)
