"""KVE models — Series 105. Frozen dataclasses throughout (house
convention).

`HypothesisOccurrence` is KVE's own, LOCAL, plain translation of one
real day's worth of Series 101-104 artefact references -- NOT an import
of those packages' own types (same sibling-isolation convention Series
104 established, taken to the same zero-bujji-import conclusion)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class HypothesisOccurrence:
    """One real, disclosed instance of a candidate pattern on one real
    day. Every field is a real, caller-supplied reference to an
    already-computed Series 101-104 artefact -- KVE never invents an
    occurrence, it only receives and measures them."""
    day: str
    timestamp: str
    market_classification: str        # real, disclosed label the caller assigns (e.g. from a real MarketPhenomenaReport).
    evidence_packet_id: Optional[str]
    counterfactual_session_id: Optional[str]
    counterfactual_legal: bool        # real CounterfactualSession.replay_legality == LEGAL, caller-translated.
    opportunity_assessment_id: Optional[str]
    opportunity_classification: Optional[str]  # real OAE classification for this day, if one exists.
    phenomena_report_id: Optional[str]
    causally_valid: bool              # real, disclosed -- was this occurrence's evidence causally valid (no future info)?
    replay_reproducible: bool         # real, disclosed -- was this occurrence's result reproduced via a real replay?


@dataclass(frozen=True)
class KnowledgeValidationExplanation:
    validation_id: str
    why_this_state: Tuple[str, ...]
    metrics_considered: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class KnowledgeValidationReport:
    """The one real output KVE produces per real hypothesis. Contains NO
    recommendation, NO Knowledge Candidate, NO Engineering Proposal --
    only a validation state and the real, measured evidence behind it."""
    validation_id: str
    hypothesis_label: str             # real, disclosed, plain-language description of the candidate pattern.
    generated_timestamp: str
    occurrence_count: int
    diversity_count: int              # count of distinct real market_classification values across occurrences.
    consistency: str                  # taxonomy.ALL_CONSISTENCY_STATES
    consistency_ratio: float          # real fraction of occurrences that were causally_valid AND counterfactual_legal.
    replay_support_ratio: float       # real fraction of occurrences that were replay_reproducible.
    causal_validity_ratio: float      # real fraction of occurrences that were causally_valid.
    evidence_growth: str              # taxonomy.ALL_TRENDS
    evidence_decay: str               # taxonomy.ALL_TRENDS
    validation_state: str             # taxonomy.ALL_VALIDATION_STATES -- exactly one.
    supporting_evidence_packets: Tuple[str, ...]
    supporting_opportunity_assessments: Tuple[str, ...]
    supporting_counterfactual_sessions: Tuple[str, ...]
    supporting_phenomena: Tuple[str, ...]
    historical_occurrence_statistics: Tuple[Tuple[str, int], ...]  # (day, cumulative_occurrence_count) pairs, real, ordered.
    explanation: KnowledgeValidationExplanation
    provenance: str
    schema_version: str
