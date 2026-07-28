"""MLE serialization — Series 100, Phase 1.0. Pure dict/JSON round-trip,
mirrors every prior MSI package's convention."""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import (
    EvidenceDimensions, KnowledgeCandidate, KnowledgeCandidateExplanation, LifecycleTransition,
)


def dimensions_to_dict(d: EvidenceDimensions) -> Dict[str, Any]:
    return {
        "repeatability": d.repeatability, "consistency": d.consistency,
        "causal_validity": d.causal_validity, "replay_support": d.replay_support,
        "market_diversity": d.market_diversity,
    }


def dimensions_from_dict(d: Dict[str, Any]) -> EvidenceDimensions:
    return EvidenceDimensions(
        repeatability=d["repeatability"], consistency=d["consistency"],
        causal_validity=d["causal_validity"], replay_support=d["replay_support"],
        market_diversity=d["market_diversity"],
    )


def transition_to_dict(t: LifecycleTransition) -> Dict[str, Any]:
    return {
        "transition_id": t.transition_id, "from_stage": t.from_stage, "to_stage": t.to_stage,
        "timestamp": t.timestamp, "reasoning": list(t.reasoning), "schema_version": t.schema_version,
    }


def transition_from_dict(d: Dict[str, Any]) -> LifecycleTransition:
    return LifecycleTransition(
        transition_id=d["transition_id"], from_stage=d["from_stage"], to_stage=d["to_stage"],
        timestamp=d["timestamp"], reasoning=tuple(d["reasoning"]), schema_version=d["schema_version"],
    )


def explanation_to_dict(e: KnowledgeCandidateExplanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id, "why_this_observation": list(e.why_this_observation),
        "why_this_tier": list(e.why_this_tier), "why_this_stage": list(e.why_this_stage),
        "schema_version": e.schema_version,
    }


def explanation_from_dict(d: Dict[str, Any]) -> KnowledgeCandidateExplanation:
    return KnowledgeCandidateExplanation(
        assessment_id=d["assessment_id"], why_this_observation=tuple(d["why_this_observation"]),
        why_this_tier=tuple(d["why_this_tier"]), why_this_stage=tuple(d["why_this_stage"]),
        schema_version=d["schema_version"],
    )


def candidate_to_dict(c: KnowledgeCandidate) -> Dict[str, Any]:
    return {
        "candidate_id": c.candidate_id, "observation": c.observation, "reasoning": list(c.reasoning),
        "supporting_evidence": list(c.supporting_evidence), "earliest_causal_timestamp": c.earliest_causal_timestamp,
        "counterfactual_analysis": c.counterfactual_analysis, "replay_support": list(c.replay_support),
        "consistency": c.consistency, "evidence_strength": c.evidence_strength,
        "evidence_dimensions": dimensions_to_dict(c.evidence_dimensions),
        "implementation_status": c.implementation_status,
        "lifecycle_history": [transition_to_dict(t) for t in c.lifecycle_history],
        "occurrence_count": c.occurrence_count, "explanation": explanation_to_dict(c.explanation),
        "provenance": c.provenance, "schema_version": c.schema_version,
    }


def candidate_from_dict(d: Dict[str, Any]) -> KnowledgeCandidate:
    return KnowledgeCandidate(
        candidate_id=d["candidate_id"], observation=d["observation"], reasoning=tuple(d["reasoning"]),
        supporting_evidence=tuple(d["supporting_evidence"]), earliest_causal_timestamp=d["earliest_causal_timestamp"],
        counterfactual_analysis=d["counterfactual_analysis"], replay_support=tuple(d["replay_support"]),
        consistency=d["consistency"], evidence_strength=d["evidence_strength"],
        evidence_dimensions=dimensions_from_dict(d["evidence_dimensions"]),
        implementation_status=d["implementation_status"],
        lifecycle_history=tuple(transition_from_dict(t) for t in d["lifecycle_history"]),
        occurrence_count=d["occurrence_count"], explanation=explanation_from_dict(d["explanation"]),
        provenance=d["provenance"], schema_version=d["schema_version"],
    )


def candidate_to_json(c: KnowledgeCandidate) -> str:
    return json.dumps(candidate_to_dict(c), sort_keys=True, default=repr)


def candidate_from_json(text: str) -> KnowledgeCandidate:
    return candidate_from_dict(json.loads(text))
