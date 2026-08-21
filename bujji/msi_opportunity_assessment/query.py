"""OAE query — Series 104. Pure, read-only lookups, mirrors every prior
MSI package's query.py convention."""
from __future__ import annotations

from typing import Sequence, Tuple

from . import taxonomy
from .models import OpportunityAssessment


def by_classification(assessments: Sequence[OpportunityAssessment], classification: str) -> Tuple[OpportunityAssessment, ...]:
    return tuple(a for a in assessments if a.classification == classification)


def opportunities_identified(assessments: Sequence[OpportunityAssessment]) -> Tuple[OpportunityAssessment, ...]:
    return by_classification(assessments, taxonomy.CLASSIFICATION_OPPORTUNITY_IDENTIFIED)


def trades_that_should_not_have_occurred(assessments: Sequence[OpportunityAssessment]) -> Tuple[OpportunityAssessment, ...]:
    return by_classification(assessments, taxonomy.CLASSIFICATION_TRADE_SHOULD_NOT_HAVE_OCCURRED)


def classification_distribution(assessments: Sequence[OpportunityAssessment]) -> dict:
    """Real, disclosed count per real classification -- a plain tally,
    never a ranked or weighted summary (that would start to look like a
    recommendation, which this package must never produce)."""
    return {c: len(by_classification(assessments, c)) for c in taxonomy.ALL_CLASSIFICATIONS}
