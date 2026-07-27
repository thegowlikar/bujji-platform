"""Evaluation Comparator — BUJJI Options OS, Integration Series 3,
Sprint 1.

Pure, descriptive statistics over a sequence of `IntelligenceEvaluation`
records. Every function here is deterministic given its input list and
touches nothing else -- no I/O, no clock, no adapter. Deliberately
excludes anything resembling trading performance: no profitability, no
edge, no win rate, no expectancy is computed anywhere in this module.
"""
from __future__ import annotations

from typing import Optional, Sequence

from .policy import ABSTAINED, AGREED, DISAGREED, IntelligenceEvaluation, UNKNOWN


def agreement_rate(evaluations: Sequence[IntelligenceEvaluation]) -> Optional[float]:
    decided = [e for e in evaluations if e.outcome in (AGREED, DISAGREED)]
    if not decided:
        return None
    return sum(1 for e in decided if e.outcome == AGREED) / len(decided)


def disagreement_rate(evaluations: Sequence[IntelligenceEvaluation]) -> Optional[float]:
    decided = [e for e in evaluations if e.outcome in (AGREED, DISAGREED)]
    if not decided:
        return None
    return sum(1 for e in decided if e.outcome == DISAGREED) / len(decided)


def abstention_rate(evaluations: Sequence[IntelligenceEvaluation]) -> Optional[float]:
    if not evaluations:
        return None
    return sum(1 for e in evaluations if e.outcome == ABSTAINED) / len(evaluations)


def unknown_rate(evaluations: Sequence[IntelligenceEvaluation]) -> Optional[float]:
    if not evaluations:
        return None
    return sum(1 for e in evaluations if e.outcome == UNKNOWN) / len(evaluations)


def coverage_percentage(evaluations: Sequence[IntelligenceEvaluation]) -> Optional[float]:
    """Fraction of evaluations where sufficient intelligence was
    available to render ANY verdict (AGREED, DISAGREED, or ABSTAINED),
    as opposed to INSUFFICIENT_INTELLIGENCE or UNKNOWN."""
    if not evaluations:
        return None
    covered = sum(1 for e in evaluations if e.outcome in (AGREED, DISAGREED, ABSTAINED))
    return covered / len(evaluations)


def comparator_summary(evaluations: Sequence[IntelligenceEvaluation]) -> dict:
    return {
        "total": len(evaluations),
        "agreement_rate": agreement_rate(evaluations),
        "disagreement_rate": disagreement_rate(evaluations),
        "abstention_rate": abstention_rate(evaluations),
        "unknown_rate": unknown_rate(evaluations),
        "coverage_percentage": coverage_percentage(evaluations),
    }
