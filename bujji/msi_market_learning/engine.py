"""MLE engine — Series 100, Phase 1.0. Pure functions: no IO, no state,
no wall-clock reads, no randomness -- mirrors every other MSI engine.py
in this project. This module builds and mutates (via pure, functional
replacement -- every dataclass is frozen) Knowledge Candidates. It NEVER
calls any Production decision function, NEVER places an order, and NEVER
reads or writes anything under bujji.broker/bujji.execution/etc -- the
only real inputs are strings, ids, and timestamps the caller supplies.

Isolation note: this module has ZERO imports from any Production package
(no msi_strategy_selector, no msi_portfolio_construction, no broker, no
live_pipeline_bridge). It is deliberately import-blind to Production --
callers translate real Production/Series-99 data into plain strings/ids
before calling anything here, exactly like this project's established
sibling-isolation convention (see msi_strategy_selector's own
ExpressionAssessmentView pattern, Sprint 120)."""
from __future__ import annotations

import hashlib
from typing import Sequence, Tuple

from . import config as _config
from . import taxonomy
from .models import EvidenceDimensions, KnowledgeCandidate, KnowledgeCandidateExplanation, LifecycleTransition


def _candidate_id(observation: str, earliest_causal_timestamp: str, schema_version: str) -> str:
    content = "|".join([observation, earliest_causal_timestamp, schema_version])
    return "KC-" + hashlib.md5(content.encode("utf-8")).hexdigest()[:24]


def _transition_id(candidate_id: str, from_stage: str, to_stage: str, timestamp: str) -> str:
    content = "|".join([candidate_id, from_stage, to_stage, timestamp])
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def compute_evidence_tier(occurrence_count: int) -> str:
    """Declarative, non-tunable lookup (config.py's own disclosed ladder).
    Returns the HIGHEST tier whose threshold occurrence_count has reached
    or passed."""
    tier = taxonomy.TIER_NONE
    for threshold, name in _config.EVIDENCE_TIER_THRESHOLDS:
        if occurrence_count >= threshold:
            tier = name
    return tier


def new_candidate(
    observation: str, reasoning: Sequence[str], supporting_evidence: Sequence[str],
    earliest_causal_timestamp: str, market_classification: str, *, timestamp: str,
    schema_version: str = _config.SCHEMA_VERSION,
) -> KnowledgeCandidate:
    """Creates exactly one new Knowledge Candidate at LIFECYCLE_OBSERVED,
    occurrence_count=1. Never called automatically by anything -- the
    caller (a future analysis layer, out of scope for Phase 1.0, or a
    human/test constructing one directly) supplies every real input."""
    aid = _candidate_id(observation, earliest_causal_timestamp, schema_version)
    dims = EvidenceDimensions(
        repeatability=1, consistency=taxonomy.CONSISTENCY_UNKNOWN, causal_validity=True,
        replay_support=False, market_diversity=1,
    )
    transition = LifecycleTransition(
        transition_id=_transition_id(aid, "NONE", taxonomy.LIFECYCLE_OBSERVED, timestamp),
        from_stage="NONE", to_stage=taxonomy.LIFECYCLE_OBSERVED, timestamp=timestamp,
        reasoning=("first real occurrence observed",), schema_version=schema_version,
    )
    explanation = KnowledgeCandidateExplanation(
        assessment_id=aid,
        why_this_observation=tuple(reasoning),
        why_this_tier=(f"occurrence_count=1 -> tier={taxonomy.TIER_NONE} (below the WEAK threshold of "
                        f"{_config.EVIDENCE_TIER_THRESHOLDS[1][0]})",),
        why_this_stage=("a single observation is always LIFECYCLE_OBSERVED -- no stage may be skipped",),
        schema_version=schema_version,
    )
    diversity_marker = f"classification_seen:{market_classification}"
    seeded_reasoning = tuple(reasoning) + (diversity_marker,)
    return KnowledgeCandidate(
        candidate_id=aid, observation=observation, reasoning=seeded_reasoning,
        supporting_evidence=tuple(supporting_evidence), earliest_causal_timestamp=earliest_causal_timestamp,
        counterfactual_analysis=None, replay_support=(), consistency=taxonomy.CONSISTENCY_UNKNOWN,
        evidence_strength=taxonomy.TIER_NONE, evidence_dimensions=dims,
        implementation_status=taxonomy.LIFECYCLE_OBSERVED, lifecycle_history=(transition,),
        occurrence_count=1, explanation=explanation,
        provenance=_config.DEFAULT_PROVENANCE, schema_version=schema_version,
    )


def record_occurrence(
    candidate: KnowledgeCandidate, new_evidence_id: str, market_classification: str, *, timestamp: str,
) -> KnowledgeCandidate:
    """Real, functional (non-mutating) update: one more real, disclosed
    occurrence of the same pattern. Never called with a fabricated
    evidence_id -- the caller supplies a real Series-99 id."""
    new_count = candidate.occurrence_count + 1
    new_tier = compute_evidence_tier(new_count)
    # market_diversity only increases when this occurrence's classification
    # differs from what's already been recorded -- tracked via a real,
    # disclosed marker embedded in reasoning (Phase 1.0 keeps this simple
    # and auditable; a dedicated per-candidate classification set is a
    # natural Phase 1.1 extension once real analysis output exists).
    diversity_marker = f"classification_seen:{market_classification}"
    is_new_classification = diversity_marker not in candidate.reasoning
    new_market_diversity = candidate.evidence_dimensions.market_diversity + (1 if is_new_classification else 0)
    new_reasoning = candidate.reasoning + (diversity_marker,) if is_new_classification else candidate.reasoning

    new_dims = EvidenceDimensions(
        repeatability=new_count, consistency=candidate.evidence_dimensions.consistency,
        causal_validity=candidate.evidence_dimensions.causal_validity,
        replay_support=candidate.evidence_dimensions.replay_support,
        market_diversity=new_market_diversity,
    )
    new_explanation = KnowledgeCandidateExplanation(
        assessment_id=candidate.candidate_id,
        why_this_observation=candidate.explanation.why_this_observation,
        why_this_tier=(f"occurrence_count={new_count} -> tier={new_tier}",),
        why_this_stage=candidate.explanation.why_this_stage,
        schema_version=candidate.schema_version,
    )
    return KnowledgeCandidate(
        candidate_id=candidate.candidate_id, observation=candidate.observation,
        reasoning=new_reasoning,
        supporting_evidence=candidate.supporting_evidence + (new_evidence_id,),
        earliest_causal_timestamp=candidate.earliest_causal_timestamp,
        counterfactual_analysis=candidate.counterfactual_analysis, replay_support=candidate.replay_support,
        consistency=candidate.consistency, evidence_strength=new_tier, evidence_dimensions=new_dims,
        implementation_status=candidate.implementation_status, lifecycle_history=candidate.lifecycle_history,
        occurrence_count=new_count, explanation=new_explanation,
        provenance=candidate.provenance, schema_version=candidate.schema_version,
    )


def advance_lifecycle(candidate: KnowledgeCandidate, to_stage: str, reasoning: Sequence[str], *, timestamp: str) -> KnowledgeCandidate:
    """Advances exactly one real, allowed lifecycle step. Raises ValueError
    on any attempt to skip a stage -- 'no stage may be skipped' is
    enforced here structurally, not by convention."""
    from_stage = candidate.implementation_status
    if not taxonomy.is_allowed_transition(from_stage, to_stage):
        raise ValueError(
            f"illegal lifecycle transition: {from_stage} -> {to_stage} is not the next stage "
            f"in taxonomy.ALL_LIFECYCLE_STAGES and is not one of the disclosed ACTIVE/DECAYING exceptions."
        )
    transition = LifecycleTransition(
        transition_id=_transition_id(candidate.candidate_id, from_stage, to_stage, timestamp),
        from_stage=from_stage, to_stage=to_stage, timestamp=timestamp,
        reasoning=tuple(reasoning), schema_version=candidate.schema_version,
    )
    new_explanation = KnowledgeCandidateExplanation(
        assessment_id=candidate.candidate_id,
        why_this_observation=candidate.explanation.why_this_observation,
        why_this_tier=candidate.explanation.why_this_tier,
        why_this_stage=tuple(reasoning),
        schema_version=candidate.schema_version,
    )
    return KnowledgeCandidate(
        candidate_id=candidate.candidate_id, observation=candidate.observation, reasoning=candidate.reasoning,
        supporting_evidence=candidate.supporting_evidence, earliest_causal_timestamp=candidate.earliest_causal_timestamp,
        counterfactual_analysis=candidate.counterfactual_analysis, replay_support=candidate.replay_support,
        consistency=candidate.consistency, evidence_strength=candidate.evidence_strength,
        evidence_dimensions=candidate.evidence_dimensions,
        implementation_status=to_stage, lifecycle_history=candidate.lifecycle_history + (transition,),
        occurrence_count=candidate.occurrence_count, explanation=new_explanation,
        provenance=candidate.provenance, schema_version=candidate.schema_version,
    )


def assess_decay(candidate: KnowledgeCandidate, recent_occurrence_timestamps: Sequence[str], all_occurrence_timestamps: Sequence[str]) -> str:
    """Real, disclosed, non-tunable comparison of recent vs. lifetime
    occurrence rate (config.py's DECAY_* constants). Never removes
    knowledge -- only classifies it; the caller decides whether to flag it
    for engineering review (per the spec: 'never automatically remove
    knowledge')."""
    if len(all_occurrence_timestamps) < 2 or len(recent_occurrence_timestamps) < 2:
        return taxonomy.DECAY_UNKNOWN
    recent_window = recent_occurrence_timestamps[-_config.DECAY_RECENT_WINDOW_OCCURRENCES:]
    if len(recent_window) < 2:
        return taxonomy.DECAY_UNKNOWN

    def _rate(timestamps: Sequence[str]) -> float:
        span_days = max(1, (_days_between(timestamps[0], timestamps[-1])))
        return len(timestamps) / span_days

    lifetime_rate = _rate(all_occurrence_timestamps)
    recent_rate = _rate(recent_window)
    if lifetime_rate <= 0:
        return taxonomy.DECAY_UNKNOWN
    ratio = recent_rate / lifetime_rate
    if ratio >= _config.DECAY_IMPROVING_RATIO:
        return taxonomy.DECAY_IMPROVING
    if ratio <= _config.DECAY_WEAKENING_RATIO:
        return taxonomy.DECAY_WEAKENING
    return taxonomy.DECAY_STABLE


def _days_between(iso_a: str, iso_b: str) -> int:
    from datetime import date as _date
    da = _date.fromisoformat(iso_a[:10])
    db = _date.fromisoformat(iso_b[:10])
    return abs((db - da).days)
