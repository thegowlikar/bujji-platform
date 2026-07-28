"""MLE models — Series 100, Phase 1.0. Frozen dataclasses throughout
(house convention). MLE produces Knowledge Candidates, never "missed
trades" or recommendations -- per the Series 100 spec's own Knowledge
Model section."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class EvidenceDimensions:
    """The five real, disclosed dimensions the spec requires evidence
    strength to depend on: repeatability, consistency, causal validity,
    replay support, market diversity. Each is a real, measured value --
    never a single blended score (that would hide which dimension is
    actually weak)."""
    repeatability: int                     # real occurrence_count.
    consistency: str                       # taxonomy.ALL_CONSISTENCY_STATES.
    causal_validity: bool                  # True iff every citation passed the contemporaneous-evidence check.
    replay_support: bool                   # True iff at least one real replay run corroborates the pattern.
    market_diversity: int                  # count of DISTINCT real market-day classifications this candidate has been observed under.


@dataclass(frozen=True)
class LifecycleTransition:
    """One real, recorded step in a Knowledge Candidate's lifecycle --
    append-only history, never overwritten (mirrors this project's
    established append-only journal convention)."""
    transition_id: str
    from_stage: str
    to_stage: str
    timestamp: str
    reasoning: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class KnowledgeCandidateExplanation:
    assessment_id: str
    why_this_observation: Tuple[str, ...]
    why_this_tier: Tuple[str, ...]
    why_this_stage: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class KnowledgeCandidate:
    """The one real unit of MLE's output. Every field the Series 100 spec
    requires is present; nothing here ever triggers a Production write --
    enforced structurally, see isolation.py."""
    candidate_id: str                      # md5 content hash -- deterministic, never uuid4()/wall-clock.
    observation: str                       # one-sentence, plain-language, disclosed statement.
    reasoning: Tuple[str, ...]
    supporting_evidence: Tuple[str, ...]   # real bujji.msi_decision_auditor ids (decision_id/outcome_id/pair_id) -- traceability.
    earliest_causal_timestamp: str         # the earliest real timestamp at which this pattern's evidence existed.
    counterfactual_analysis: Optional[str]  # populated only once a Counterfactual Engine exists (a later phase,
                                            # explicitly out of scope for Phase 1.0's 10 deliverables) -- always
                                            # None in Phase 1.0, field present so the schema never needs to change.
    replay_support: Tuple[str, ...]        # real day-strings ("YYYY-MM-DD") that corroborate this pattern under replay.
    consistency: str                       # taxonomy.ALL_CONSISTENCY_STATES.
    evidence_strength: str                 # taxonomy.ALL_EVIDENCE_TIERS.
    evidence_dimensions: EvidenceDimensions
    implementation_status: str             # taxonomy.ALL_LIFECYCLE_STAGES -- current stage.
    lifecycle_history: Tuple[LifecycleTransition, ...]  # every real, recorded transition, append-only.
    occurrence_count: int
    explanation: KnowledgeCandidateExplanation
    provenance: str
    schema_version: str
