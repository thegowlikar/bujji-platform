"""Trade Thesis Engine serialization — Series 92. Pure dict round-trip,
mirrors every prior MSI package's convention."""
from __future__ import annotations

from typing import Any, Dict

from .models import Explanation, TradeThesisAssessment


def explanation_to_dict(e: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id, "why_this_thesis": list(e.why_this_thesis),
        "supporting_evidence": list(e.supporting_evidence), "conflicting_evidence": list(e.conflicting_evidence),
        "what_would_invalidate": list(e.what_would_invalidate), "schema_version": e.schema_version,
    }


def assessment_to_dict(a: TradeThesisAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id, "timestamp": a.timestamp, "thesis_type": a.thesis_type,
        "market_expectation": a.market_expectation, "expected_move": a.expected_move,
        "expected_time_horizon": a.expected_time_horizon, "volatility_expectation": a.volatility_expectation,
        "directional_expectation": a.directional_expectation, "conviction": a.conviction,
        "invalidation_conditions": list(a.invalidation_conditions),
        "supporting_domains": list(a.supporting_domains), "conflicting_domains": list(a.conflicting_domains),
        "explanation": explanation_to_dict(a.explanation), "provenance": a.provenance,
        "schema_version": a.schema_version,
    }
