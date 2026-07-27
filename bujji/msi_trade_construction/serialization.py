"""Trade Construction Foundation serialization — Series 90. Pure
dict round-trip, mirrors every prior MSI package's convention exactly."""
from __future__ import annotations

from typing import Any, Dict

from .models import Explanation, ExpiryDecision, StrikeLeg, TradeConstructionAssessment


def expiry_decision_to_dict(d: ExpiryDecision) -> Dict[str, Any]:
    return {
        "chosen_expiry": d.chosen_expiry, "dte": d.dte,
        "candidate_expiries": list(d.candidate_expiries),
        "rejected_expiries": [list(t) for t in d.rejected_expiries],
        "reasoning": list(d.reasoning),
    }


def leg_to_dict(leg: StrikeLeg) -> Dict[str, Any]:
    return {
        "role": leg.role, "option_type": leg.option_type, "strike": leg.strike, "expiry": leg.expiry,
        "delta": leg.delta, "premium": leg.premium, "open_interest": leg.open_interest,
        "side": leg.side, "ratio": leg.ratio, "reasoning": list(leg.reasoning),
    }


def explanation_to_dict(e: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id, "why_this_expiry": list(e.why_this_expiry),
        "why_these_strikes": list(e.why_these_strikes),
        "why_not_neighbouring_strikes": list(e.why_not_neighbouring_strikes),
        "dominant_constraints": list(e.dominant_constraints), "schema_version": e.schema_version,
    }


def assessment_to_dict(a: TradeConstructionAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id, "timestamp": a.timestamp, "strategy_family": a.strategy_family,
        "constructed": a.constructed, "rejection_reason": a.rejection_reason,
        "expiry": a.expiry, "expiry_decision": expiry_decision_to_dict(a.expiry_decision),
        "legs": [leg_to_dict(leg) for leg in a.legs],
        "entry_reference_prices": dict(a.entry_reference_prices),
        "expected_credit_debit": a.expected_credit_debit, "risk_profile": a.risk_profile,
        "required_margin": a.required_margin, "margin_unavailable_reason": a.margin_unavailable_reason,
        "supporting_assessment_ids": list(a.supporting_assessment_ids),
        "explanation": explanation_to_dict(a.explanation),
        "provenance": a.provenance, "schema_version": a.schema_version,
    }
