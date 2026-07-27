"""Trade Thesis Engine query helpers — Series 92. Pure, read-only
lookups, mirroring every prior MSI package's query.py convention."""
from __future__ import annotations

from typing import Optional, Sequence

from .models import TradeThesisAssessment


def by_id(assessments: Sequence[TradeThesisAssessment], assessment_id: str) -> Optional[TradeThesisAssessment]:
    for a in assessments:
        if a.assessment_id == assessment_id:
            return a
    return None


def by_thesis_type(assessments: Sequence[TradeThesisAssessment], thesis_type: str) -> tuple:
    return tuple(a for a in assessments if a.thesis_type == thesis_type)


def by_conviction(assessments: Sequence[TradeThesisAssessment], conviction: str) -> tuple:
    return tuple(a for a in assessments if a.conviction == conviction)


def latest(assessments: Sequence[TradeThesisAssessment]) -> Optional[TradeThesisAssessment]:
    if not assessments:
        return None
    return max(assessments, key=lambda a: a.timestamp)
