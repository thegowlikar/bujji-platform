"""Strategy Expression Engine serialization — Series 93. Pure dict
round-trip, mirrors every prior MSI package's convention."""
from __future__ import annotations

from typing import Any, Dict

from bujji.msi_trade_thesis.serialization import assessment_to_dict as thesis_to_dict

from .models import Explanation, StrategyExpressionAssessment


def explanation_to_dict(e: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id, "why_this_expression": list(e.why_this_expression),
        "why_families_compatible": list(e.why_families_compatible),
        "why_families_incompatible": list(e.why_families_incompatible), "schema_version": e.schema_version,
    }


def assessment_to_dict(a: StrategyExpressionAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id, "timestamp": a.timestamp, "thesis": thesis_to_dict(a.thesis),
        "desired_direction": a.desired_direction, "desired_volatility_exposure": a.desired_volatility_exposure,
        "desired_risk_profile": a.desired_risk_profile, "desired_time_decay": a.desired_time_decay,
        "desired_convexity": a.desired_convexity, "required_characteristics": list(a.required_characteristics),
        "forbidden_characteristics": list(a.forbidden_characteristics),
        "compatible_strategy_families": list(a.compatible_strategy_families),
        "incompatible_strategy_families": list(a.incompatible_strategy_families),
        "explanation": explanation_to_dict(a.explanation), "provenance": a.provenance,
        "schema_version": a.schema_version,
    }
