"""Position Construction Intelligence serialization — Series 95. Pure
dict round-trip, mirrors every prior MSI package's convention."""
from __future__ import annotations

from typing import Any, Dict

from .models import Explanation, ExpiryPlan, PositionConstructionAssessment, StrikePlan, WingPlan


def expiry_plan_to_dict(p: ExpiryPlan) -> Dict[str, Any]:
    return {"rule": p.rule, "min_dte": p.min_dte, "max_dte": p.max_dte, "reasoning": list(p.reasoning)}


def strike_plan_to_dict(p: StrikePlan) -> Dict[str, Any]:
    return {"target_delta": p.target_delta, "reasoning": list(p.reasoning)}


def wing_plan_to_dict(p: WingPlan) -> Dict[str, Any]:
    return {"plan": p.plan, "width_source": p.width_source, "reasoning": list(p.reasoning)}


def explanation_to_dict(e: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id, "why_this_construction_style": list(e.why_this_construction_style),
        "why_this_expiry_philosophy": list(e.why_this_expiry_philosophy),
        "why_this_strike_philosophy": list(e.why_this_strike_philosophy),
        "evidence_that_drove_the_design": list(e.evidence_that_drove_the_design), "schema_version": e.schema_version,
    }


def assessment_to_dict(a: PositionConstructionAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id, "timestamp": a.timestamp,
        "selected_strategy_family": a.selected_strategy_family, "construction_type": a.construction_type,
        "expiry_plan": expiry_plan_to_dict(a.expiry_plan), "strike_plan": strike_plan_to_dict(a.strike_plan),
        "wing_plan": wing_plan_to_dict(a.wing_plan), "risk_profile": a.risk_profile,
        "payoff_profile": a.payoff_profile, "adjustment_readiness": a.adjustment_readiness,
        "expected_delta": a.expected_delta, "expected_gamma": a.expected_gamma,
        "expected_theta": a.expected_theta, "expected_vega": a.expected_vega,
        "supporting_assessment_ids": list(a.supporting_assessment_ids),
        "explanation": explanation_to_dict(a.explanation), "provenance": a.provenance,
        "schema_version": a.schema_version,
    }
