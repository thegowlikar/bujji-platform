"""Capital Utilization Intelligence — BUJJI Options OS v3, Numeric Risk
Governor Gate C.2.

Two pure, deterministic pieces:

  assess_capital_utilization(available_capital, required_margin, ...)
      -> CapitalUtilizationReport
      "How much of available capital would this consume, and is that
      healthy?" A pure percentage-threshold classifier -- HEALTHY /
      WARNING / BLOCKED -- entirely separate from, and NEVER a
      substitute for, capital_check.assess_capital's own ALLOW/VETO
      decision. This answers "how close to the edge are we", not
      "should this trade happen" -- that remains capital_check.py's
      exclusive authority, unconditionally.

  explain_trade_decision(capital_assessment, margin_explanation,
      utilization_report) -> TradeDecisionExplanation
      Combines Gate B's real capital_check.CapitalCheckAssessment (the
      SINGLE SOURCE OF TRUTH for ALLOW/VETO) with the margin/
      utilization intelligence layers to produce a human-readable
      reason. `decision` and `blocking_reason` are copied VERBATIM
      from capital_assessment -- this function can never recompute,
      override, upgrade, or downgrade that outcome. Risk
      classification/capital status here are explanatory context only.
      This is the one deliberate exception to "risk classification
      cannot bypass capital checks": it can never bypass, but it CAN
      relabel an already-VETOed decision's classification to
      EXCESSIVE_CAPITAL_USAGE when that's the more specific reason --
      purely descriptive, changes no outcome.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from bujji.trading_brain.risk_governor.capital_check import CapitalCheckAssessment
from bujji.trading_brain.risk_governor.simulated_margin_provider import (
    MarginExplanation,
    RISK_EXCESSIVE_CAPITAL_USAGE,
)

UTILIZATION_HEALTHY = "HEALTHY"
UTILIZATION_WARNING = "WARNING"
UTILIZATION_BLOCKED = "BLOCKED"

DEFAULT_HEALTHY_THRESHOLD = 0.5
DEFAULT_WARNING_THRESHOLD = 0.75


@dataclass(frozen=True)
class CapitalUtilizationReport:
    available_capital: float
    required_margin: float
    usage_fraction: float     # required_margin / available_capital; float('inf') if available_capital<=0 and required_margin>0
    status: str                # HEALTHY | WARNING | BLOCKED


def assess_capital_utilization(
    available_capital: float,
    required_margin: float,
    healthy_threshold: float = DEFAULT_HEALTHY_THRESHOLD,
    warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
) -> CapitalUtilizationReport:
    """Pure. Raises ValueError on malformed input (None/negative
    capital or margin, inconsistent thresholds) rather than silently
    producing a meaningless percentage -- this is a supporting
    explanatory utility, not part of the Governor's own ALLOW/VETO
    machinery, so raising (not a VETO-shaped return) is the correct
    fail-closed behavior here; the caller decides what an exception
    means for their own flow.

    Thresholds are configurable, matching the requested rule set
    (usage < 50% HEALTHY, 50-75% WARNING, >75% BLOCKED by default):
    usage_fraction < healthy_threshold -> HEALTHY
    healthy_threshold <= usage_fraction <= warning_threshold -> WARNING
    usage_fraction > warning_threshold -> BLOCKED

    available_capital <= 0 is a special case, checked before dividing:
    if required_margin is also 0 (nothing needed, nothing available),
    that is HEALTHY (there is genuinely nothing at risk) -- but if
    required_margin > 0 with no capital at all, that is unconditionally
    BLOCKED regardless of threshold configuration (usage_fraction is
    reported as float('inf'), never silently treated as 0%)."""
    if available_capital is None:
        raise ValueError("assess_capital_utilization requires a non-None available_capital")
    if required_margin is None:
        raise ValueError("assess_capital_utilization requires a non-None required_margin")
    if required_margin < 0:
        raise ValueError(f"required_margin must be non-negative, got {required_margin!r}")
    if not (0.0 <= healthy_threshold <= warning_threshold):
        raise ValueError(
            "thresholds must satisfy 0 <= healthy_threshold <= warning_threshold, "
            f"got healthy={healthy_threshold!r} warning={warning_threshold!r}"
        )

    if available_capital <= 0:
        if required_margin == 0:
            return CapitalUtilizationReport(
                available_capital=available_capital, required_margin=required_margin,
                usage_fraction=0.0, status=UTILIZATION_HEALTHY,
            )
        return CapitalUtilizationReport(
            available_capital=available_capital, required_margin=required_margin,
            usage_fraction=float("inf"), status=UTILIZATION_BLOCKED,
        )

    usage_fraction = required_margin / available_capital
    if usage_fraction < healthy_threshold:
        status = UTILIZATION_HEALTHY
    elif usage_fraction <= warning_threshold:
        status = UTILIZATION_WARNING
    else:
        status = UTILIZATION_BLOCKED

    return CapitalUtilizationReport(
        available_capital=available_capital, required_margin=required_margin,
        usage_fraction=usage_fraction, status=status,
    )


@dataclass(frozen=True)
class TradeDecisionExplanation:
    """decision and blocking_reason are always copied VERBATIM from
    the real CapitalCheckAssessment this was built from -- never
    recomputed, never independently derived. Everything else here is
    explanatory context layered on top of that authoritative outcome."""

    decision: str                     # "ALLOW" | "VETO" -- verbatim from capital_assessment.decision
    blocking_reason: Optional[str]    # verbatim from capital_assessment.blocking_reason
    reason: str                        # human-readable summary
    primary_risk: Optional[str]
    risk_classification: str
    capital_status: str
    current_margin: Optional[float]
    available_capital: Optional[float]


def _format_usage(usage_fraction: float) -> str:
    if math.isinf(usage_fraction):
        return "an unbounded amount of"
    return f"{usage_fraction:.0%} of"


def explain_trade_decision(
    capital_assessment: CapitalCheckAssessment,
    margin_explanation: MarginExplanation,
    utilization_report: CapitalUtilizationReport,
) -> TradeDecisionExplanation:
    risk_classification = margin_explanation.risk_classification
    if capital_assessment.decision == "VETO" and utilization_report.status == UTILIZATION_BLOCKED:
        # Relabels the EXPLANATION only -- capital_assessment.decision (already VETO) is untouched.
        risk_classification = RISK_EXCESSIVE_CAPITAL_USAGE

    usage_phrase = _format_usage(utilization_report.usage_fraction)

    if capital_assessment.decision == "ALLOW":
        primary_risk = "NAKED_SHORT_EXPOSURE" if "NAKED_SHORT_EXPOSURE" in margin_explanation.risk_flags else None
        reason = f"Capital utilization would reach {usage_phrase} available capital (status={utilization_report.status})."
    elif utilization_report.status == UTILIZATION_BLOCKED:
        primary_risk = "NAKED_SHORT_EXPOSURE" if margin_explanation.naked_exposure > 0 else "CAPITAL_UTILIZATION_EXCEEDS_POLICY"
        reason = f"Capital utilization would reach {usage_phrase} available capital, exceeding policy (status={utilization_report.status})."
    else:
        primary_risk = capital_assessment.blocking_reason
        reason = f"Blocked by capital check: {capital_assessment.blocking_reason}."

    return TradeDecisionExplanation(
        decision=capital_assessment.decision,
        blocking_reason=capital_assessment.blocking_reason,
        reason=reason,
        primary_risk=primary_risk,
        risk_classification=risk_classification,
        capital_status=utilization_report.status,
        current_margin=margin_explanation.total_required_margin,
        available_capital=utilization_report.available_capital,
    )
