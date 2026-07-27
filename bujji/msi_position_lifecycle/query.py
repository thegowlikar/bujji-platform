"""Position Lifecycle Intelligence query helpers — Series 96. Pure,
read-only lookups, mirroring every prior MSI package's query.py
convention."""
from __future__ import annotations

from typing import Optional, Sequence

from .models import PositionLifecycleAssessment


def by_id(assessments: Sequence[PositionLifecycleAssessment], lifecycle_id: str) -> Optional[PositionLifecycleAssessment]:
    for a in assessments:
        if a.lifecycle_id == lifecycle_id:
            return a
    return None


def by_state(assessments: Sequence[PositionLifecycleAssessment], state: str) -> tuple:
    return tuple(a for a in assessments if a.position_state == state)


def latest(assessments: Sequence[PositionLifecycleAssessment]) -> Optional[PositionLifecycleAssessment]:
    if not assessments:
        return None
    return max(assessments, key=lambda a: a.timestamp)
