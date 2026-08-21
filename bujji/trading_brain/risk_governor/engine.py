"""Numeric Risk Governor — assess(). BUJJI Options OS v3.

Composes Gate A's position-group lifecycle state, Gate B's defined-risk
assessment, portfolio-level numeric limits, and the capital/margin
check into ONE RiskVerdict -- a hard dispatch veto, never advice. All
four checks always run (never short-circuit after the first failure),
so both failed_checks and passed_checks are always complete and every
verdict can fully explain itself -- same convention as every other
decision-table engine in this codebase (risk_brain, runtime_safety,
etc.).

Not wired into any dispatch path. No production_runtime import. No
broker import. This module has exactly one job: given already-computed
Gate A/B/portfolio-limit/capital results, produce one ALLOW/VETO
verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Tuple

from bujji.trading_brain.risk_governor.capital_check import CapitalCheckAssessment
from bujji.trading_brain.risk_governor.defined_risk import DefinedRiskAssessment
from bujji.trading_brain.risk_governor.portfolio_limits import PortfolioLimitAssessment
from bujji.trading_brain.risk_governor.position_group_fold import (
    LIFECYCLE_CONSTRUCTED,
    LIFECYCLE_OPEN,
    LIFECYCLE_PARTIALLY_OPEN,
    PositionGroupState,
)

Clock = Callable[[], datetime]

_DISPATCH_ELIGIBLE_LIFECYCLE_STATES = (LIFECYCLE_CONSTRUCTED, LIFECYCLE_OPEN, LIFECYCLE_PARTIALLY_OPEN)


@dataclass(frozen=True)
class RiskVerdict:
    decision: str                      # "ALLOW" | "VETO"
    blocking_reason: str                # first-triggered, fixed priority order; "" when ALLOW
    failed_checks: Tuple[str, ...]
    passed_checks: Tuple[str, ...]
    decision_trace: str
    evaluated_at: datetime


class MismatchedAssessmentError(ValueError):
    """Raised when defined_risk's position_group_id disagrees with
    group_state's -- a caller bug (e.g. accidentally passing group B's
    defined-risk assessment while assessing group A) that must fail
    loud, never silently produce a verdict for the wrong group. Audited
    and confirmed as a real, silent-authorization-of-the-wrong-group
    risk before this check existed."""


def assess(
    group_state: PositionGroupState,
    defined_risk: DefinedRiskAssessment,
    portfolio_limits: PortfolioLimitAssessment,
    capital_check: CapitalCheckAssessment,
    clock: Clock,
) -> RiskVerdict:
    if defined_risk.position_group_id != group_state.position_group_id:
        raise MismatchedAssessmentError(
            f"group_state.position_group_id={group_state.position_group_id!r} but "
            f"defined_risk.position_group_id={defined_risk.position_group_id!r} -- "
            f"these must refer to the same group"
        )

    failed: List[str] = []
    passed: List[str] = []

    # Check 1: Gate A lifecycle -- the group must actually be in a state
    # a risk decision could ever apply to. CONSTRUCTED is included
    # deliberately -- pre-trade entry authorization runs BEFORE anything
    # has been submitted, so a freshly-constructed, nothing-yet-attempted
    # group is exactly the state a NEW entry's risk decision is made
    # from (defined_risk.py falls back to each leg's requested_quantity
    # in this case, since net_quantity is always 0 pre-fill).
    # UNRESOLVED/MINTED/PENDING_FINAL_RECONCILIATION/ABORTED/CLOSED remain
    # non-eligible: MINTED/UNRESOLVED/PENDING_FINAL_RECONCILIATION are
    # mid-flight or unresolved states with nothing stable to assess yet,
    # and CLOSED/ABORTED are terminal.
    if group_state.lifecycle_state in _DISPATCH_ELIGIBLE_LIFECYCLE_STATES:
        passed.append("LIFECYCLE_ELIGIBLE")
    else:
        failed.append(f"LIFECYCLE_NOT_ELIGIBLE_{group_state.lifecycle_state}")

    # Check 2: Gate B defined risk.
    if defined_risk.decision == "ALLOW":
        passed.append("DEFINED_RISK_WITHIN_BOUNDS")
    else:
        failed.append(f"DEFINED_RISK_{defined_risk.blocking_reason}")

    # Check 3: portfolio-level numeric limits.
    if portfolio_limits.decision == "ALLOW":
        passed.append("PORTFOLIO_LIMITS_WITHIN_BOUNDS")
    else:
        failed.append(f"PORTFOLIO_LIMITS_{portfolio_limits.blocking_reason}")

    # Check 4: capital/margin -- always fails closed today (no certified
    # whole-book margin provider exists in this codebase yet; see
    # capital_check.py's own docstring).
    if capital_check.decision == "ALLOW":
        passed.append("CAPITAL_WITHIN_BOUNDS")
    else:
        failed.append(f"CAPITAL_{capital_check.blocking_reason}")

    if failed:
        decision = "VETO"
        blocking_reason = failed[0]
    else:
        decision = "ALLOW"
        blocking_reason = ""

    trace = (
        f"Lifecycle={group_state.lifecycle_state}. "
        f"DefinedRisk={defined_risk.decision}"
        f"{'(' + defined_risk.blocking_reason + ')' if defined_risk.blocking_reason else ''}. "
        f"PortfolioLimits={portfolio_limits.decision}"
        f"{'(' + portfolio_limits.blocking_reason + ')' if portfolio_limits.blocking_reason else ''}. "
        f"Capital={capital_check.decision}"
        f"{'(' + capital_check.blocking_reason + ')' if capital_check.blocking_reason else ''}. "
        f"Verdict={decision}."
    )

    return RiskVerdict(
        decision=decision, blocking_reason=blocking_reason,
        failed_checks=tuple(failed), passed_checks=tuple(passed),
        decision_trace=trace, evaluated_at=clock(),
    )
