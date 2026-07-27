"""Position Lifecycle Intelligence serialization — Series 96. Pure dict
round-trip, mirrors every prior MSI package's convention."""
from __future__ import annotations

from typing import Any, Dict

from .models import (
    AdjustmentPolicy, EmergencyPolicy, Explanation, ExpiryPolicy, LossPolicy,
    PositionLifecycleAssessment, ProfitPolicy, ThesisInvalidation,
)


def adjustment_policy_to_dict(p: AdjustmentPolicy) -> Dict[str, Any]:
    return {
        "triggers": list(p.triggers), "actionable_triggers_today": list(p.actionable_triggers_today),
        "monitoring_only_triggers": list(p.monitoring_only_triggers), "fired": list(p.fired),
        "reasoning": list(p.reasoning),
    }


def profit_policy_to_dict(p: ProfitPolicy) -> Dict[str, Any]:
    return {"harvest_rule": p.harvest_rule, "reasoning": list(p.reasoning)}


def loss_policy_to_dict(p: LossPolicy) -> Dict[str, Any]:
    return {"accept_loss_rule": p.accept_loss_rule, "reasoning": list(p.reasoning)}


def expiry_policy_to_dict(p: ExpiryPolicy) -> Dict[str, Any]:
    return {"rule": p.rule, "dte_remaining": p.dte_remaining, "reasoning": list(p.reasoning)}


def emergency_policy_to_dict(p: EmergencyPolicy) -> Dict[str, Any]:
    return {"trigger": p.trigger, "reasoning": list(p.reasoning)}


def thesis_invalidation_to_dict(t: ThesisInvalidation) -> Dict[str, Any]:
    return {
        "entry_thesis_type": t.entry_thesis_type, "current_thesis_type": t.current_thesis_type,
        "compatible": t.compatible, "reasoning": list(t.reasoning),
    }


def explanation_to_dict(e: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id, "why_this_adjustment_policy": list(e.why_this_adjustment_policy),
        "why_this_profit_policy": list(e.why_this_profit_policy),
        "why_this_invalidation_rule": list(e.why_this_invalidation_rule),
        "why_this_emergency_policy": list(e.why_this_emergency_policy), "schema_version": e.schema_version,
    }


def assessment_to_dict(a: PositionLifecycleAssessment) -> Dict[str, Any]:
    return {
        "lifecycle_id": a.lifecycle_id, "timestamp": a.timestamp, "strategy_family": a.strategy_family,
        "construction_type": a.construction_type, "position_state": a.position_state,
        "expected_lifetime": a.expected_lifetime, "monitoring_requirements": list(a.monitoring_requirements),
        "adjustment_policy": adjustment_policy_to_dict(a.adjustment_policy),
        "profit_policy": profit_policy_to_dict(a.profit_policy), "loss_policy": loss_policy_to_dict(a.loss_policy),
        "expiry_policy": expiry_policy_to_dict(a.expiry_policy),
        "emergency_policy": emergency_policy_to_dict(a.emergency_policy),
        "thesis_invalidation": thesis_invalidation_to_dict(a.thesis_invalidation),
        "supporting_assessment_ids": list(a.supporting_assessment_ids),
        "explanation": explanation_to_dict(a.explanation), "provenance": a.provenance,
        "schema_version": a.schema_version,
    }
