"""JSON round-trip for StrategyDecision / StrategyEvaluation."""
from __future__ import annotations

from typing import Any, Dict, List

from .models import StrategyDecision, StrategyEvaluation


def evaluation_to_dict(e: StrategyEvaluation) -> Dict[str, Any]:
    return {
        "strategy_id": e.strategy_id,
        "eligibility": e.eligibility,
        "supporting_conditions": list(e.supporting_conditions),
        "rejecting_conditions": list(e.rejecting_conditions),
    }


def evaluation_from_dict(d: Dict[str, Any]) -> StrategyEvaluation:
    return StrategyEvaluation(
        strategy_id=d["strategy_id"],
        eligibility=d["eligibility"],
        supporting_conditions=tuple(d["supporting_conditions"]),
        rejecting_conditions=tuple(d["rejecting_conditions"]),
    )


def decision_to_dict(d: StrategyDecision) -> Dict[str, Any]:
    return {
        "decision_id": d.decision_id,
        "selected_strategy": d.selected_strategy,
        "selection_status": d.selection_status,
        "selection_confidence": d.selection_confidence,
        "selection_reason": d.selection_reason,
        "supporting_conditions": list(d.supporting_conditions),
        "rejecting_conditions": list(d.rejecting_conditions),
        "alternative_candidates": list(d.alternative_candidates),
        "all_evaluations": [evaluation_to_dict(e) for e in d.all_evaluations],
        "decision_trace": d.decision_trace,
        "market_state_assessment_id": d.market_state_assessment_id,
        "timestamp": d.timestamp,
        "version": d.version,
    }


def decision_from_dict(d: Dict[str, Any]) -> StrategyDecision:
    evaluations: List[StrategyEvaluation] = [evaluation_from_dict(e) for e in d["all_evaluations"]]
    return StrategyDecision(
        decision_id=d["decision_id"],
        selected_strategy=d["selected_strategy"],
        selection_status=d["selection_status"],
        selection_confidence=d["selection_confidence"],
        selection_reason=d["selection_reason"],
        supporting_conditions=tuple(d["supporting_conditions"]),
        rejecting_conditions=tuple(d["rejecting_conditions"]),
        alternative_candidates=tuple(d["alternative_candidates"]),
        all_evaluations=tuple(evaluations),
        decision_trace=d["decision_trace"],
        market_state_assessment_id=d.get("market_state_assessment_id"),
        timestamp=d["timestamp"],
        version=d["version"],
    )
