"""Margin Bridge & Capital Fidelity serialization — Series 97. Pure
dict round-trip, mirrors every prior MSI package's convention."""
from __future__ import annotations

from typing import Any, Dict

from .models import Explanation, MarginEstimate


def explanation_to_dict(e: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id, "why_this_estimate_exists": list(e.why_this_estimate_exists),
        "why_exact_margin_is_or_isnt_available": list(e.why_exact_margin_is_or_isnt_available),
        "assumptions_required": list(e.assumptions_required), "schema_version": e.schema_version,
    }


def assessment_to_dict(a: MarginEstimate) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id, "timestamp": a.timestamp, "methodology": a.methodology,
        "estimated_margin": a.estimated_margin, "confidence": a.confidence, "data_source": a.data_source,
        "replay_safe": a.replay_safe, "explanation": explanation_to_dict(a.explanation),
        "provenance": a.provenance, "schema_version": a.schema_version,
    }
