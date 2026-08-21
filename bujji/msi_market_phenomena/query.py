"""MPC query — Series 103. Pure, read-only lookups, mirrors every prior
MSI package's query.py convention."""
from __future__ import annotations

from typing import Sequence, Tuple

from .models import MarketPhenomenaReport, Phenomenon


def phenomena_of_type(report: MarketPhenomenaReport, phenomenon_type: str) -> Tuple[Phenomenon, ...]:
    return tuple(p for p in report.phenomena if p.phenomenon_type == phenomenon_type)


def by_confidence(report: MarketPhenomenaReport, confidence: str) -> Tuple[Phenomenon, ...]:
    return tuple(p for p in report.phenomena if p.confidence == confidence)


def days_with_phenomenon(reports: Sequence[MarketPhenomenaReport], phenomenon_type: str) -> Tuple[str, ...]:
    """Real, disclosed corpus-wide lookup: every real day (across a real
    set of reports) where a given phenomenon was detected -- the common
    vocabulary Series 100+ can use without re-deriving classification."""
    return tuple(sorted({r.day for r in reports if phenomena_of_type(r, phenomenon_type)}))
