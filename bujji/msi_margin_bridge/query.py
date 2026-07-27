"""Margin Bridge & Capital Fidelity query helpers — Series 97. Pure,
read-only lookups, mirroring every prior MSI package's query.py
convention."""
from __future__ import annotations

from typing import Optional, Sequence

from .models import MarginEstimate


def by_id(assessments: Sequence[MarginEstimate], assessment_id: str) -> Optional[MarginEstimate]:
    for a in assessments:
        if a.assessment_id == assessment_id:
            return a
    return None


def by_confidence(assessments: Sequence[MarginEstimate], confidence: str) -> tuple:
    return tuple(a for a in assessments if a.confidence == confidence)


def replay_safe_only(assessments: Sequence[MarginEstimate]) -> tuple:
    return tuple(a for a in assessments if a.replay_safe)


def latest(assessments: Sequence[MarginEstimate]) -> Optional[MarginEstimate]:
    if not assessments:
        return None
    return max(assessments, key=lambda a: a.timestamp)
