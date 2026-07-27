"""Deterministic JSON round-trip for StrategySelectionAssessment."""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import CandidateScore, Explanation, StrategySelectionAssessment


def candidate_score_to_dict(c: CandidateScore) -> Dict[str, Any]:
    return {
        "strategy_family": c.strategy_family, "disqualified": c.disqualified,
        "disqualifying_states": list(c.disqualifying_states),
        "matched_preferred_states": list(c.matched_preferred_states),
        "matched_acceptable_states": list(c.matched_acceptable_states),
        "match_score": c.match_score,
    }


def candidate_score_from_dict(d: Dict[str, Any]) -> CandidateScore:
    return CandidateScore(
        strategy_family=d["strategy_family"], disqualified=d["disqualified"],
        disqualifying_states=tuple(d["disqualifying_states"]),
        matched_preferred_states=tuple(d["matched_preferred_states"]),
        matched_acceptable_states=tuple(d["matched_acceptable_states"]),
        match_score=d["match_score"],
    )


def explanation_to_dict(e: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id,
        "why_this_strategy": list(e.why_this_strategy),
        "why_not_alternatives": list(e.why_not_alternatives),
        "evidence_that_mattered_most": list(e.evidence_that_mattered_most),
        "evidence_that_prevented_alternatives": list(e.evidence_that_prevented_alternatives),
        "active_market_states": list(e.active_market_states),
        "schema_version": e.schema_version,
    }


def explanation_from_dict(d: Dict[str, Any]) -> Explanation:
    return Explanation(
        assessment_id=d["assessment_id"], why_this_strategy=tuple(d["why_this_strategy"]),
        why_not_alternatives=tuple(d["why_not_alternatives"]),
        evidence_that_mattered_most=tuple(d["evidence_that_mattered_most"]),
        evidence_that_prevented_alternatives=tuple(d["evidence_that_prevented_alternatives"]),
        active_market_states=tuple(d["active_market_states"]), schema_version=d["schema_version"],
    )


def assessment_to_dict(a: StrategySelectionAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id, "timestamp": a.timestamp,
        "selected_strategy_family": a.selected_strategy_family,
        "alternative_candidates": [candidate_score_to_dict(c) for c in a.alternative_candidates],
        "rejection_reasons": list(a.rejection_reasons),
        "supporting_evidence": list(a.supporting_evidence),
        "confidence": a.confidence,
        "explanation": explanation_to_dict(a.explanation),
        "provenance": a.provenance, "schema_version": a.schema_version,
    }


def assessment_from_dict(d: Dict[str, Any]) -> StrategySelectionAssessment:
    return StrategySelectionAssessment(
        assessment_id=d["assessment_id"], timestamp=d["timestamp"],
        selected_strategy_family=d["selected_strategy_family"],
        alternative_candidates=tuple(candidate_score_from_dict(c) for c in d["alternative_candidates"]),
        rejection_reasons=tuple(d["rejection_reasons"]),
        supporting_evidence=tuple(d["supporting_evidence"]),
        confidence=d["confidence"], explanation=explanation_from_dict(d["explanation"]),
        provenance=d["provenance"], schema_version=d["schema_version"],
    )


def assessment_to_json(a: StrategySelectionAssessment) -> str:
    return json.dumps(assessment_to_dict(a), sort_keys=True, default=repr)


def assessment_from_json(text: str) -> StrategySelectionAssessment:
    return assessment_from_dict(json.loads(text))
