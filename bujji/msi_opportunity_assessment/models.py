"""OAE models — Series 104. Frozen dataclasses throughout (house
convention).

The three `*View` dataclasses are OAE's own, LOCAL, plain translations
of real Series 99/101/102/103 artefacts -- NOT imports of those
packages' own types. This mirrors the sibling-isolation convention
already established throughout this project (e.g.
msi_strategy_selector.ExpressionAssessmentView, Sprint 120): a
caller-supplied translation, never a direct sibling import. It is what
makes OAE the most isolated package in the whole learning stack -- zero
imports from any other bujji package anywhere in this file or engine.py."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class DecisionView:
    """Local translation of a real bujji.msi_decision_auditor.DecisionRecord."""
    decision_id: str
    date: str
    timestamp: str
    decision_outcome: str            # taxonomy.DECISION_OUTCOME_*
    strategy_family: Optional[str]
    confidence: str
    conflicting_domains: Tuple[str, ...]  # real, contemporaneous TradeThesisAssessment.conflicting_domains pass-through -- NEVER hindsight.


@dataclass(frozen=True)
class PhenomenaView:
    """Local translation of a real bujji.msi_market_phenomena.MarketPhenomenaReport."""
    report_id: str
    day: str
    phenomenon_types: Tuple[str, ...]


@dataclass(frozen=True)
class CounterfactualView:
    """Local translation of a real bujji.msi_counterfactual_replay.CounterfactualSession."""
    session_id: str
    replay_legality: str             # taxonomy.REPLAY_LEGALITY_*
    baseline_selected_family: Optional[str]
    alternative_selected_family: Optional[str]
    earliest_causal_timestamp: str


@dataclass(frozen=True)
class OpportunityAssessmentExplanation:
    assessment_id: str
    why_this_classification: Tuple[str, ...]
    why_not_alternatives: Tuple[str, ...]
    opportunity_criteria_checked: Tuple[str, ...]  # every one of the 6 real criteria, pass/fail, disclosed.
    schema_version: str


@dataclass(frozen=True)
class OpportunityAssessment:
    """The one real output OAE produces per real trading day. Contains
    NO recommendation, NO Knowledge Candidate, NO Engineering Proposal --
    only a classification and the real, disclosed evidence behind it."""
    assessment_id: str
    day: str
    timestamp: str
    classification: str              # taxonomy.ALL_CLASSIFICATIONS -- exactly one.
    earliest_causal_timestamp: str
    supporting_market_phenomena: Tuple[str, ...]     # real PhenomenaView.report_id references.
    supporting_counterfactual_session: Optional[str]  # real CounterfactualView.session_id, if used.
    supporting_evidence_packets: Tuple[str, ...]     # real EvidencePacket.packet_id references.
    supporting_decision_records: Tuple[str, ...]     # real DecisionView.decision_id references.
    supporting_reasoning: Tuple[str, ...]
    evidence_strength: str           # taxonomy.ALL_CONFIDENCE_LEVELS
    replay_references: Tuple[str, ...]
    assumptions: Tuple[str, ...]
    explanation: OpportunityAssessmentExplanation
    provenance: str
    schema_version: str
