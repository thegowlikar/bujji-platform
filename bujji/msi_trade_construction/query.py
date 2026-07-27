"""Trade Construction Foundation query helpers — Series 90. Pure,
read-only lookups over a sequence of TradeConstructionAssessment,
mirroring `msi_strategy_selector/query.py` exactly."""
from __future__ import annotations

from typing import Optional, Sequence

from .models import TradeConstructionAssessment


def by_id(assessments: Sequence[TradeConstructionAssessment], assessment_id: str) -> Optional[TradeConstructionAssessment]:
    for a in assessments:
        if a.assessment_id == assessment_id:
            return a
    return None


def by_strategy_family(assessments: Sequence[TradeConstructionAssessment], family: str) -> tuple:
    return tuple(a for a in assessments if a.strategy_family == family)


def constructed_only(assessments: Sequence[TradeConstructionAssessment]) -> tuple:
    return tuple(a for a in assessments if a.constructed)


def rejected_only(assessments: Sequence[TradeConstructionAssessment]) -> tuple:
    return tuple(a for a in assessments if not a.constructed)


def latest(assessments: Sequence[TradeConstructionAssessment]) -> Optional[TradeConstructionAssessment]:
    if not assessments:
        return None
    return max(assessments, key=lambda a: a.timestamp)
