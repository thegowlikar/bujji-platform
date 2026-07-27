"""Portfolio & Risk Construction query helpers — Series 91. Pure,
read-only lookups, mirroring `msi_trade_construction/query.py` exactly."""
from __future__ import annotations

from typing import Optional, Sequence

from .models import PortfolioConstructionAssessment


def by_id(assessments: Sequence[PortfolioConstructionAssessment], assessment_id: str) -> Optional[PortfolioConstructionAssessment]:
    for a in assessments:
        if a.assessment_id == assessment_id:
            return a
    return None


def approved_only(assessments: Sequence[PortfolioConstructionAssessment]) -> tuple:
    return tuple(a for a in assessments if a.approval_state == "APPROVED")


def rejected_only(assessments: Sequence[PortfolioConstructionAssessment]) -> tuple:
    return tuple(a for a in assessments if a.approval_state == "REJECTED")


def deferred_only(assessments: Sequence[PortfolioConstructionAssessment]) -> tuple:
    return tuple(a for a in assessments if a.approval_state == "DEFERRED")


def by_rejection_reason(assessments: Sequence[PortfolioConstructionAssessment], reason: str) -> tuple:
    return tuple(a for a in assessments if reason in a.rejection_reasons)


def latest(assessments: Sequence[PortfolioConstructionAssessment]) -> Optional[PortfolioConstructionAssessment]:
    if not assessments:
        return None
    return max(assessments, key=lambda a: a.timestamp)
