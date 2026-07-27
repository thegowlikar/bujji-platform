"""bujji.msi_dynamic_management.models — Series 109. Frozen dataclasses."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Explanation:
    """Deliverable 7: every field named explicitly, never folded into a
    single generic 'why' -- a recommendation that cannot answer all five
    is answering an incomplete question."""
    assessment_id: str
    why: Tuple[str, ...]
    why_now: Tuple[str, ...]
    why_not_later: Tuple[str, ...]
    why_not_another_roll: Tuple[str, ...]
    why_not_exit: Tuple[str, ...]
    evidence_used: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class DecisionAssessment:
    """Deliverable 3: the shape shared by all six independent decision
    types (Strike Roll, Expiry Roll, Delta Rebalance, Wing Adjustment,
    Strategy Conversion, Full Exit). Each is produced independently --
    nothing here combines two decision types into one object."""
    assessment_id: str
    decision_type: str          # taxonomy.ALL_DECISION_TYPES
    recommended: bool
    priority: str                # taxonomy.ALL_PRIORITIES -- evidence-computed, never fixed
    priority_evidence: Tuple[str, ...]
    lifecycle_id: str
    timestamp: str
    explanation: Explanation
    provenance: str
    schema_version: str


@dataclass(frozen=True)
class TransitionAssessment:
    assessment_id: str
    from_family: str
    representable: bool
    to_family: Optional[str]
    reasoning: Tuple[str, ...]
    timestamp: str
    explanation: Explanation
    provenance: str
    schema_version: str


@dataclass(frozen=True)
class DynamicManagementBoard:
    """A read-only convenience aggregate of the six independent
    assessments for one position on one real day -- never a merged or
    collapsed decision; the six `DecisionAssessment`s remain the source
    of truth, this is only a bundle for callers/dashboards."""
    lifecycle_id: str
    timestamp: str
    strike_roll: DecisionAssessment
    expiry_roll: DecisionAssessment
    delta_rebalance: DecisionAssessment
    wing_adjustment: DecisionAssessment
    strategy_conversion: DecisionAssessment
    full_exit: DecisionAssessment
    transition: TransitionAssessment
