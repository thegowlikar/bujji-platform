"""Capital Brain engine — BUJJI Options OS v3, Engineering Series 36,
Sprint 1.

The Risk Brain answered "should this trade be allowed?" This module
answers a deliberately narrower question: "assuming it is allowed,
what capital POLICY should be authorized?" It authorizes a policy, not
an execution plan -- no lot count, no quantity, no margin figure, no
broker exposure number, no account balance read exists anywhere in
this package.

Input is exactly one `RiskAssessment` (or `None`) -- never the
Strategy Selector, the Market State Builder, MIC v2, a broker, an
order, a replay engine, or execution of any kind.

Evaluation is a finite, deterministic decision table. No probability,
no PnL, no Kelly/Sharpe, no expectancy estimation, no machine
learning, and no randomness appear anywhere in this function.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, Optional, Tuple

from ..risk_brain.models import RiskAssessment
from . import taxonomy
from .models import CapitalDecision

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def _downgrade_one_step(level: str) -> str:
    idx = taxonomy.CONFIDENCE_ORDER.index(level)
    if level == "UNKNOWN":
        return "UNKNOWN"
    return taxonomy.CONFIDENCE_ORDER[max(idx - 1, 1)]


def _result(
    capital_intent: str,
    allocation_status: str,
    allocation_reason: str,
    constraints: Tuple[str, ...],
    controls: Tuple[str, ...],
    confidence: str,
    risk_assessment: Optional[RiskAssessment],
    timestamp: str,
) -> CapitalDecision:
    trace = (
        f"Risk Approval = {risk_assessment.approval if risk_assessment else 'NONE'}. "
        f"Risk Level = {risk_assessment.risk_level if risk_assessment else 'UNKNOWN'}. "
        f"Capital Intent = {capital_intent}. "
        f"Constraints = {', '.join(constraints)}. "
        f"Allocation Status = {allocation_status}."
    )
    seed = "|".join(
        [
            risk_assessment.assessment_id if risk_assessment else "NONE",
            capital_intent,
            allocation_status,
            timestamp,
        ]
    )
    decision_id = "CD-" + hashlib.md5(seed.encode()).hexdigest()[:16]
    return CapitalDecision(
        decision_id=decision_id,
        capital_intent=capital_intent,
        allocation_status=allocation_status,
        allocation_reason=allocation_reason,
        allocation_constraints=constraints,
        required_controls=controls,
        confidence=confidence,
        decision_trace=trace,
        risk_assessment_id=risk_assessment.assessment_id if risk_assessment else None,
        timestamp=timestamp,
        version=taxonomy.CAPITAL_BRAIN_VERSION,
    )


def authorize(
    risk_assessment: Optional[RiskAssessment],
    clock: Clock = _real_clock,
) -> CapitalDecision:
    """Translate an approved risk decision into a capital policy.

    Never calculates a quantity, a lot, a margin figure, or a broker
    exposure number -- only names a finite capital intent, allocation
    status, and set of constraints/controls for a future Execution
    Planner to interpret.
    """
    timestamp = clock().isoformat()

    # Rule 1: nothing to authorize a policy from.
    if risk_assessment is None:
        return _result(
            taxonomy.CAPITAL_INTENT_UNKNOWN,
            taxonomy.ALLOCATION_STATUS_UNKNOWN,
            "No RiskAssessment was supplied.",
            (taxonomy.CONSTRAINT_NONE,),
            (taxonomy.REQUIRED_CONTROL_NONE,),
            "UNKNOWN",
            None,
            timestamp,
        )

    # Rule 7 (priority override): EXTREME risk always denies capital,
    # regardless of what the approval field itself says. This module
    # never trusts an ALLOW/ALLOW_WITH_CONTROLS paired with EXTREME
    # risk -- a combination the Risk Brain's own policy never actually
    # produces (see Series 35), but this module never trusts an
    # upstream object blindly.
    if risk_assessment.risk_level == "EXTREME":
        return _result(
            taxonomy.CAPITAL_INTENT_NONE,
            taxonomy.ALLOCATION_STATUS_DENIED,
            "Risk Level is EXTREME; no capital may be committed regardless of approval.",
            (taxonomy.CONSTRAINT_MANUAL_REVIEW,),
            (taxonomy.REQUIRED_CONTROL_NONE,),
            _downgrade_one_step(risk_assessment.confidence),
            risk_assessment,
            timestamp,
        )

    approval = risk_assessment.approval

    # Rule 2: denied outright.
    if approval == "DENY":
        return _result(
            taxonomy.CAPITAL_INTENT_NONE,
            taxonomy.ALLOCATION_STATUS_DENIED,
            "Risk Brain denied approval; no capital may be committed.",
            (taxonomy.CONSTRAINT_NONE,),
            (taxonomy.REQUIRED_CONTROL_NONE,),
            risk_assessment.confidence,
            risk_assessment,
            timestamp,
        )

    # Insufficient evidence upstream -- nothing to authorize.
    if approval == "UNKNOWN":
        return _result(
            taxonomy.CAPITAL_INTENT_UNKNOWN,
            taxonomy.ALLOCATION_STATUS_UNKNOWN,
            "Risk Brain could not reach an approval determination.",
            (taxonomy.CONSTRAINT_NONE,),
            (taxonomy.REQUIRED_CONTROL_NONE,),
            risk_assessment.confidence,
            risk_assessment,
            timestamp,
        )

    # Rule 3: approved only under the Risk Brain's own named controls.
    if approval == "ALLOW_WITH_CONTROLS":
        return _result(
            taxonomy.CAPITAL_INTENT_REDUCED,
            taxonomy.ALLOCATION_STATUS_LIMITED,
            "Risk Brain approved with controls; capital is reduced and must follow those controls.",
            (taxonomy.CONSTRAINT_REDUCE_EXPOSURE,),
            (taxonomy.REQUIRED_CONTROL_FOLLOW_RISK_CONTROLS,),
            _downgrade_one_step(risk_assessment.confidence),
            risk_assessment,
            timestamp,
        )

    # Rules 4-6: clean ALLOW, graded by risk level.
    if approval == "ALLOW":
        risk_level = risk_assessment.risk_level

        if risk_level == "LOW":
            return _result(
                taxonomy.CAPITAL_INTENT_STANDARD,
                taxonomy.ALLOCATION_STATUS_APPROVED,
                "Risk Level is LOW under a clean ALLOW; standard capital is authorized.",
                (taxonomy.CONSTRAINT_NONE,),
                (taxonomy.REQUIRED_CONTROL_NONE,),
                risk_assessment.confidence,
                risk_assessment,
                timestamp,
            )

        if risk_level == "MODERATE":
            return _result(
                taxonomy.CAPITAL_INTENT_REDUCED,
                taxonomy.ALLOCATION_STATUS_LIMITED,
                "Risk Level is MODERATE; capital is reduced and exposure limits apply.",
                (taxonomy.CONSTRAINT_REDUCE_EXPOSURE,),
                (taxonomy.REQUIRED_CONTROL_FOLLOW_LIMITS,),
                _downgrade_one_step(risk_assessment.confidence),
                risk_assessment,
                timestamp,
            )

        if risk_level == "HIGH":
            return _result(
                taxonomy.CAPITAL_INTENT_MINIMAL,
                taxonomy.ALLOCATION_STATUS_LIMITED,
                "Risk Level is HIGH; only minimal capital is authorized, capped to a single position.",
                (taxonomy.CONSTRAINT_MAX_SINGLE_POSITION,),
                (taxonomy.REQUIRED_CONTROL_FOLLOW_LIMITS,),
                _downgrade_one_step(risk_assessment.confidence),
                risk_assessment,
                timestamp,
            )

        # Defensive: ALLOW paired with an unrecognized/UNKNOWN risk
        # level -- structurally rare, never trusted into a real
        # authorization.
        return _result(
            taxonomy.CAPITAL_INTENT_NONE,
            taxonomy.ALLOCATION_STATUS_DENIED,
            f"Approval is ALLOW but Risk Level ({risk_level}) is not a recognized graded level; capital is denied pending review.",
            (taxonomy.CONSTRAINT_MANUAL_REVIEW,),
            (taxonomy.REQUIRED_CONTROL_NONE,),
            "UNKNOWN",
            risk_assessment,
            timestamp,
        )

    # Defensive: an unrecognized approval value.
    return _result(
        taxonomy.CAPITAL_INTENT_UNKNOWN,
        taxonomy.ALLOCATION_STATUS_UNKNOWN,
        f"Approval value ({approval}) is not recognized; no capital policy can be authorized.",
        (taxonomy.CONSTRAINT_NONE,),
        (taxonomy.REQUIRED_CONTROL_NONE,),
        "UNKNOWN",
        risk_assessment,
        timestamp,
    )
