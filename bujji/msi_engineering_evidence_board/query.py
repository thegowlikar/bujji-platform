"""EEB query — Series 106. Pure, read-only lookups, mirrors every prior
MSI package's query.py convention."""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from . import taxonomy
from .models import EngineeringEvidenceReport


def by_decision(reports: Sequence[EngineeringEvidenceReport], decision: str) -> Tuple[EngineeringEvidenceReport, ...]:
    return tuple(r for r in reports if r.decision == decision)


def ready_for_engineering_review(reports: Sequence[EngineeringEvidenceReport]) -> Tuple[EngineeringEvidenceReport, ...]:
    return by_decision(reports, taxonomy.DECISION_READY_FOR_ENGINEERING_REVIEW)


def current_report(history: Sequence[EngineeringEvidenceReport]) -> Optional[EngineeringEvidenceReport]:
    """The real, current report for one hypothesis's full real history --
    simply the most recently generated one. ARCHIVED/SUPERSEDED reports
    ARE real, valid current states too (a hypothesis can legitimately end
    its life archived) -- this never filters them out, it only picks the
    real latest entry in time."""
    if not history:
        return None
    return max(history, key=lambda r: r.generated_timestamp)


def decision_distribution(reports: Sequence[EngineeringEvidenceReport]) -> dict:
    """Real, disclosed count per real decision -- a plain tally, never
    ranked or weighted (that would start to look like a recommendation,
    which this package must never produce)."""
    return {d: len(by_decision(reports, d)) for d in taxonomy.ALL_DECISIONS}
