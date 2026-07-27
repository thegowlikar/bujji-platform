"""Decision Auditor & Learning Observatory serialization — Series 99.
Pure dict round-trip, mirrors every prior MSI package's convention."""
from __future__ import annotations

from typing import Any, Dict

from bujji.msi_execution_planning.serialization import assessment_to_dict as execution_plan_to_dict
from bujji.msi_margin_bridge.serialization import assessment_to_dict as margin_estimate_to_dict
from bujji.msi_portfolio_construction.serialization import assessment_to_dict as portfolio_decision_to_dict
from bujji.msi_position_construction.serialization import assessment_to_dict as position_construction_to_dict
from bujji.msi_trade_thesis.serialization import assessment_to_dict as thesis_to_dict

from .models import DecisionExplanation, DecisionOutcomePair, DecisionRecord, OutcomeExplanation, OutcomeRecord


def decision_explanation_to_dict(e: DecisionExplanation) -> Dict[str, Any]:
    return {"assessment_id": e.assessment_id, "why": list(e.why), "schema_version": e.schema_version}


def decision_record_to_dict(d: DecisionRecord) -> Dict[str, Any]:
    return {
        "decision_id": d.decision_id, "timestamp": d.timestamp, "date": d.date,
        "observation_ids": list(d.observation_ids), "episode_ids": list(d.episode_ids),
        "market_direction": d.market_direction, "consensus": d.consensus, "volatility_state": d.volatility_state,
        "trade_thesis": thesis_to_dict(d.trade_thesis), "strategy_family": d.strategy_family,
        "position_construction": position_construction_to_dict(d.position_construction) if d.position_construction else None,
        "portfolio_decision": portfolio_decision_to_dict(d.portfolio_decision) if d.portfolio_decision else None,
        "lifecycle_state": d.lifecycle_state,
        "margin_assessment": margin_estimate_to_dict(d.margin_assessment) if d.margin_assessment else None,
        "execution_plan": execution_plan_to_dict(d.execution_plan) if d.execution_plan else None,
        "confidence": d.confidence, "decision_outcome": d.decision_outcome,
        "explanation": decision_explanation_to_dict(d.explanation), "provenance": d.provenance,
        "schema_version": d.schema_version,
    }


def outcome_explanation_to_dict(e: OutcomeExplanation) -> Dict[str, Any]:
    return {"assessment_id": e.assessment_id, "what_actually_happened": list(e.what_actually_happened),
            "schema_version": e.schema_version}


def outcome_record_to_dict(o: OutcomeRecord) -> Dict[str, Any]:
    return {
        "outcome_id": o.outcome_id, "timestamp": o.timestamp, "date": o.date, "close_price": o.close_price,
        "session_high": o.session_high, "session_low": o.session_low,
        "realised_movement_pct": o.realised_movement_pct, "realised_volatility": o.realised_volatility,
        "realised_direction": o.realised_direction, "thesis_survival": o.thesis_survival,
        "execution_feasibility": o.execution_feasibility, "explanation": outcome_explanation_to_dict(o.explanation),
        "provenance": o.provenance, "schema_version": o.schema_version,
    }


def pair_to_dict(p: DecisionOutcomePair) -> Dict[str, Any]:
    return {
        "pair_id": p.pair_id, "decision": decision_record_to_dict(p.decision),
        "outcome": outcome_record_to_dict(p.outcome), "provenance": p.provenance,
        "schema_version": p.schema_version,
    }
