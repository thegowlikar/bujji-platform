"""KVE query — Series 105. Pure, read-only lookups, mirrors every prior
MSI package's query.py convention."""
from __future__ import annotations

from typing import Sequence, Tuple

from . import taxonomy
from .models import KnowledgeValidationReport


def by_state(reports: Sequence[KnowledgeValidationReport], state: str) -> Tuple[KnowledgeValidationReport, ...]:
    return tuple(r for r in reports if r.validation_state == state)


def validated(reports: Sequence[KnowledgeValidationReport]) -> Tuple[KnowledgeValidationReport, ...]:
    return by_state(reports, taxonomy.STATE_VALIDATED)


def decaying(reports: Sequence[KnowledgeValidationReport]) -> Tuple[KnowledgeValidationReport, ...]:
    return by_state(reports, taxonomy.STATE_DECAYING)


def invalidated(reports: Sequence[KnowledgeValidationReport]) -> Tuple[KnowledgeValidationReport, ...]:
    return by_state(reports, taxonomy.STATE_INVALIDATED)


def state_distribution(reports: Sequence[KnowledgeValidationReport]) -> dict:
    """Real, disclosed count per real validation state -- a plain tally,
    never ranked or weighted (that would start to look like a
    recommendation, which this package must never produce)."""
    return {s: len(by_state(reports, s)) for s in taxonomy.ALL_VALIDATION_STATES}
