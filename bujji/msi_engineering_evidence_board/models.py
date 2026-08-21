"""EEB models — Series 106. Frozen dataclasses throughout (house
convention).

The `*View` dataclasses are EEB's own, LOCAL, plain translations of real
Series 99-105 artefacts -- NOT imports of those packages' own types.
Same sibling-isolation convention Series 104/105 established, taken to
its logical conclusion at the top of the whole learning stack."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class KnowledgeValidationView:
    """Local translation of a real bujji.msi_knowledge_validation.KnowledgeValidationReport."""
    validation_id: str
    hypothesis_label: str
    validation_state: str            # taxonomy.VALIDATION_STATE_*
    occurrence_count: int
    diversity_count: int
    consistency_ratio: float
    replay_support_ratio: float
    causal_validity_ratio: float
    evidence_growth: str
    evidence_decay: str


@dataclass(frozen=True)
class OpportunityAssessmentRef:
    """Local translation of one real bujji.msi_opportunity_assessment.OpportunityAssessment."""
    assessment_id: str
    classification: str              # taxonomy.OPPORTUNITY_POSITIVE_CLASSIFICATIONS or any other real OAE classification.


@dataclass(frozen=True)
class EngineeringEvidenceExplanation:
    report_id: str
    why_this_decision: Tuple[str, ...]
    criteria_evaluated: Tuple[str, ...]   # every one of the 8 real review criteria, disclosed pass/fail/observation.
    schema_version: str


@dataclass(frozen=True)
class EngineeringEvidenceReport:
    """The one real output EEB produces per real hypothesis review.
    READY_FOR_ENGINEERING_REVIEW here means only 'sufficient evidence
    exists to justify DESIGNING a controlled engineering proposal' --
    never 'implement this'. No field on this dataclass could hold code,
    a parameter value, or an implementation instruction."""
    report_id: str
    generated_timestamp: str
    hypothesis_label: str
    referenced_knowledge_validation_reports: Tuple[str, ...]
    referenced_evidence_packets: Tuple[str, ...]
    referenced_opportunity_assessments: Tuple[str, ...]
    referenced_counterfactual_sessions: Tuple[str, ...]
    referenced_phenomena_reports: Tuple[str, ...]
    referenced_decision_records: Tuple[str, ...]
    supporting_statistics: Tuple[Tuple[str, str], ...]   # (metric_name, real_value_as_string) pairs, disclosed.
    supporting_reasoning: Tuple[str, ...]
    contradictory_observations: Tuple[str, ...]           # real, disclosed -- never hidden, never smoothed over.
    known_limitations: Tuple[str, ...]                    # always non-empty -- EEB adds a standard scope note if the caller discloses none.
    decision: str                     # taxonomy.ALL_DECISIONS -- exactly one.
    explanation: EngineeringEvidenceExplanation
    provenance: str
    schema_version: str
