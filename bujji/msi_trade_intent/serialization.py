"""Deterministic JSON round-trip for TradeIntentAssessment, mirroring
`bujji.msi_strategy_eligibility.serialization`'s exact conventions
(explicit key ordering, no uuid4, no wall clock).
"""
from __future__ import annotations

import json
from typing import Any, Dict

from .models import Explanation, InvalidationCondition, TradeIntentAssessment


def invalidation_condition_to_dict(c: InvalidationCondition) -> Dict[str, Any]:
    return {
        "protected_assumption": c.protected_assumption,
        "checkable_field": c.checkable_field,
        "trigger_description": c.trigger_description,
        "source_assessment_id": c.source_assessment_id,
    }


def invalidation_condition_from_dict(d: Dict[str, Any]) -> InvalidationCondition:
    return InvalidationCondition(
        protected_assumption=d["protected_assumption"],
        checkable_field=d["checkable_field"],
        trigger_description=d["trigger_description"],
        source_assessment_id=d["source_assessment_id"],
    )


def explanation_to_dict(e: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id,
        "why_this_market_expression": e.why_this_market_expression,
        "exposures_sought": list(e.exposures_sought),
        "why_other_profiles_rejected": list(e.why_other_profiles_rejected),
        "what_would_invalidate_before_execution": list(e.what_would_invalidate_before_execution),
        "schema_version": e.schema_version,
    }


def explanation_from_dict(d: Dict[str, Any]) -> Explanation:
    return Explanation(
        assessment_id=d["assessment_id"],
        why_this_market_expression=d["why_this_market_expression"],
        exposures_sought=tuple(d["exposures_sought"]),
        why_other_profiles_rejected=tuple(d["why_other_profiles_rejected"]),
        what_would_invalidate_before_execution=tuple(d["what_would_invalidate_before_execution"]),
        schema_version=d["schema_version"],
    )


def assessment_to_dict(a: TradeIntentAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id,
        "timestamp": a.timestamp,
        "selected_strategy_family": a.selected_strategy_family,
        "intent_state": a.intent_state,
        "market_bias": a.market_bias,
        "volatility_bias": a.volatility_bias,
        "directional_exposure": a.directional_exposure,
        "premium_exposure": a.premium_exposure,
        "risk_profile": a.risk_profile,
        "invalidation_conditions": [invalidation_condition_to_dict(c) for c in a.invalidation_conditions],
        "supporting_assessment_ids": list(a.supporting_assessment_ids),
        "explanation": explanation_to_dict(a.explanation),
        "provenance": a.provenance,
        "schema_version": a.schema_version,
    }


def assessment_from_dict(d: Dict[str, Any]) -> TradeIntentAssessment:
    return TradeIntentAssessment(
        assessment_id=d["assessment_id"],
        timestamp=d["timestamp"],
        selected_strategy_family=d["selected_strategy_family"],
        intent_state=d["intent_state"],
        market_bias=d["market_bias"],
        volatility_bias=d["volatility_bias"],
        directional_exposure=d["directional_exposure"],
        premium_exposure=d["premium_exposure"],
        risk_profile=d["risk_profile"],
        invalidation_conditions=tuple(invalidation_condition_from_dict(c) for c in d["invalidation_conditions"]),
        supporting_assessment_ids=tuple(d["supporting_assessment_ids"]),
        explanation=explanation_from_dict(d["explanation"]),
        provenance=d["provenance"],
        schema_version=d["schema_version"],
    )


def assessment_to_json(a: TradeIntentAssessment) -> str:
    return json.dumps(assessment_to_dict(a), sort_keys=True, default=repr)


def assessment_from_json(text: str) -> TradeIntentAssessment:
    return assessment_from_dict(json.loads(text))
