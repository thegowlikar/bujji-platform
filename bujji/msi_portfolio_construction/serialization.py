"""Portfolio & Risk Construction serialization — Series 91. Pure dict
round-trip, mirrors every prior MSI package's convention."""
from __future__ import annotations

from typing import Any, Dict

from .models import ConcentrationReading, Explanation, HeldLeg, PortfolioConstructionAssessment


def held_leg_to_dict(leg: HeldLeg) -> Dict[str, Any]:
    return {
        "option_type": leg.option_type, "strike": leg.strike, "expiry": leg.expiry,
        "side": leg.side, "ratio": leg.ratio, "delta": leg.delta, "gamma": leg.gamma,
        "theta": leg.theta, "vega": leg.vega,
    }


def concentration_to_dict(c: ConcentrationReading) -> Dict[str, Any]:
    return {"dimension": c.dimension, "key": c.key, "count_after": c.count_after, "limit": c.limit}


def explanation_to_dict(e: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id, "why_approved": list(e.why_approved),
        "why_rejected": list(e.why_rejected), "dominant_constraint": e.dominant_constraint,
        "what_would_change_for_approval": list(e.what_would_change_for_approval),
        "schema_version": e.schema_version,
    }


def assessment_to_dict(a: PortfolioConstructionAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id, "timestamp": a.timestamp,
        "proposed_trade_assessment_id": a.proposed_trade_assessment_id, "strategy_family": a.strategy_family,
        "approval_state": a.approval_state, "rejection_reasons": list(a.rejection_reasons),
        "required_margin": a.required_margin, "estimated_margin": a.estimated_margin,
        "portfolio_delta_after": a.portfolio_delta_after, "portfolio_gamma_after": a.portfolio_gamma_after,
        "portfolio_theta_after": a.portfolio_theta_after, "portfolio_vega_after": a.portfolio_vega_after,
        "concentration_after": [concentration_to_dict(c) for c in a.concentration_after],
        "capital_required": a.capital_required, "capital_available": a.capital_available,
        "risk_budget_used": a.risk_budget_used, "position_size_lots": a.position_size_lots,
        "confidence": a.confidence, "explanation": explanation_to_dict(a.explanation),
        "provenance": a.provenance, "schema_version": a.schema_version,
    }
