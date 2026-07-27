"""Risk Brain engine — BUJJI Options OS v3, Engineering Series 35,
Sprint 1.

The Strategy Selector answered "what strategy fits today's market?"
This module answers a deliberately different question: "should this
strategy be allowed to trade today at all?" A correctly-selected
strategy can still be rejected here because the risk environment is
unacceptable -- this module never re-evaluates whether the strategy
choice itself was appropriate; that judgment belongs entirely to
Series 34.

Inputs are exactly two already-produced objects -- `StrategyDecision`
and `MarketStateAssessment` -- read only, never recomputed, never
mutated. No MIC v2 import, no broker, no execution layer, no replay
engine, no live feed, no trading history exists anywhere in this
package.

Evaluation is a finite, deterministic decision table. No probability,
no PnL, no expected value, no Kelly/Sharpe/Sortino, no Monte Carlo, no
machine learning, and no randomness appear anywhere in this function.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from ..market_state.models import MarketStateAssessment
from ..strategy_selector.models import StrategyDecision
from . import taxonomy
from .models import RiskAssessment

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def _downgrade_one_step(level: str) -> str:
    idx = taxonomy.CONFIDENCE_ORDER.index(level)
    if level == "UNKNOWN":
        return "UNKNOWN"
    return taxonomy.CONFIDENCE_ORDER[max(idx - 1, 1)]


def _bump_risk_one_step(level: str) -> str:
    idx = taxonomy.RISK_LEVEL_ORDER.index(level)
    if level == taxonomy.RISK_LEVEL_UNKNOWN:
        return taxonomy.RISK_LEVEL_UNKNOWN
    return taxonomy.RISK_LEVEL_ORDER[min(idx + 1, len(taxonomy.RISK_LEVEL_ORDER) - 1)]


def _early_result(
    status: str,
    risk_level: str,
    approval: str,
    blocking_reason: Optional[str],
    confidence: str,
    trace: str,
    strategy_decision: Optional[StrategyDecision],
    market_assessment: Optional[MarketStateAssessment],
    timestamp: str,
) -> RiskAssessment:
    seed = "|".join(
        [
            strategy_decision.decision_id if strategy_decision else "NONE",
            market_assessment.assessment_id if market_assessment else "NONE",
            status,
            approval,
            timestamp,
        ]
    )
    assessment_id = "RA-" + hashlib.md5(seed.encode()).hexdigest()[:16]
    return RiskAssessment(
        assessment_id=assessment_id,
        status=status,
        risk_level=risk_level,
        approval=approval,
        blocking_reason=blocking_reason,
        warning_reasons=(),
        required_controls=(),
        confidence=confidence,
        decision_trace=trace,
        strategy_decision_id=strategy_decision.decision_id if strategy_decision else None,
        market_state_assessment_id=market_assessment.assessment_id if market_assessment else None,
        timestamp=timestamp,
        version=taxonomy.RISK_BRAIN_VERSION,
    )


def assess(
    strategy_decision: Optional[StrategyDecision],
    market_assessment: Optional[MarketStateAssessment],
    clock: Clock = _real_clock,
) -> RiskAssessment:
    """Decide whether today's selected strategy may proceed.

    Never asks "will this make money?" -- only "is the market stable
    enough, is evidence sufficient, is confidence high enough, and is
    this strategy compatible with today's market character?"
    """
    timestamp = clock().isoformat()

    # Rule 1: missing market assessment -- nothing to reason from at all.
    if market_assessment is None:
        return _early_result(
            taxonomy.STATUS_INSUFFICIENT_EVIDENCE,
            taxonomy.RISK_LEVEL_UNKNOWN,
            taxonomy.APPROVAL_UNKNOWN,
            taxonomy.BLOCKING_REASON_INSUFFICIENT_DATA,
            "UNKNOWN",
            "INSUFFICIENT_EVIDENCE because no MarketStateAssessment was supplied.",
            strategy_decision,
            None,
            timestamp,
        )

    # Rule 2: missing StrategyDecision entirely.
    if strategy_decision is None:
        return _early_result(
            taxonomy.STATUS_REJECTED,
            taxonomy.RISK_LEVEL_UNKNOWN,
            taxonomy.APPROVAL_DENY,
            taxonomy.BLOCKING_REASON_NO_STRATEGY,
            market_assessment.confidence,
            "REJECTED because no StrategyDecision was supplied.",
            None,
            market_assessment,
            timestamp,
        )

    # Rule 3: no strategy was selected upstream (NO_STRATEGY or UNKNOWN).
    if strategy_decision.selected_strategy is None:
        return _early_result(
            taxonomy.STATUS_REJECTED,
            taxonomy.RISK_LEVEL_UNKNOWN,
            taxonomy.APPROVAL_DENY,
            taxonomy.BLOCKING_REASON_NO_STRATEGY,
            market_assessment.confidence,
            "REJECTED because the Strategy Selector did not select a strategy.",
            strategy_decision,
            market_assessment,
            timestamp,
        )

    # Rule 4: defensive consistency check -- a SELECTED status with no
    # named strategy would be an upstream contract violation. This is
    # structurally rare (Series 34 never produces it) but this module
    # never trusts upstream blindly.
    if strategy_decision.selection_status == "SELECTED" and not strategy_decision.selected_strategy:
        return _early_result(
            taxonomy.STATUS_REJECTED,
            taxonomy.RISK_LEVEL_UNKNOWN,
            taxonomy.APPROVAL_DENY,
            taxonomy.BLOCKING_REASON_UNSUPPORTED_STRATEGY,
            market_assessment.confidence,
            "REJECTED because the StrategyDecision reported a selection without naming a strategy.",
            strategy_decision,
            market_assessment,
            timestamp,
        )

    character = market_assessment.market_character

    # Rule 5 / 7: insufficient evidence to judge risk at all.
    if character == "INSUFFICIENT_EVIDENCE" or market_assessment.confidence == "UNKNOWN":
        return _early_result(
            taxonomy.STATUS_INSUFFICIENT_EVIDENCE,
            taxonomy.RISK_LEVEL_UNKNOWN,
            taxonomy.APPROVAL_UNKNOWN,
            taxonomy.BLOCKING_REASON_INSUFFICIENT_DATA,
            "UNKNOWN",
            f"INSUFFICIENT_EVIDENCE because market character is {character} or confidence is UNKNOWN.",
            strategy_decision,
            market_assessment,
            timestamp,
        )

    # Rule 3 (contradictory): UNCERTAIN market character.
    if character == "UNCERTAIN":
        return _early_result(
            taxonomy.STATUS_REJECTED,
            taxonomy.RISK_LEVEL_HIGH,
            taxonomy.APPROVAL_DENY,
            taxonomy.BLOCKING_REASON_CONTRADICTORY_EVIDENCE,
            "UNKNOWN",
            "REJECTED because Market Character is UNCERTAIN -- evidence contradicts itself too severely to trust.",
            strategy_decision,
            market_assessment,
            timestamp,
        )

    # MIXED: market_state itself undefined; nothing to evaluate a
    # specific strategy's risk against.
    if character == "MIXED":
        return _early_result(
            taxonomy.STATUS_REJECTED,
            taxonomy.RISK_LEVEL_UNKNOWN,
            taxonomy.APPROVAL_DENY,
            taxonomy.BLOCKING_REASON_UNKNOWN_MARKET,
            market_assessment.confidence,
            "REJECTED because the market's structure is undefined (Market Character MIXED).",
            strategy_decision,
            market_assessment,
            timestamp,
        )

    # Defensive: an UNKNOWN market character (a value reserved by the
    # Market State Builder's vocabulary but never actually produced by
    # its engine -- see Series 33's own disclosure). Treated the same
    # as INSUFFICIENT_EVIDENCE: this module never trusts an unfamiliar
    # combination into a false APPROVED/REJECTED verdict.
    if character == "UNKNOWN":
        return _early_result(
            taxonomy.STATUS_INSUFFICIENT_EVIDENCE,
            taxonomy.RISK_LEVEL_UNKNOWN,
            taxonomy.APPROVAL_UNKNOWN,
            taxonomy.BLOCKING_REASON_INSUFFICIENT_DATA,
            "UNKNOWN",
            "INSUFFICIENT_EVIDENCE because Market Character itself is UNKNOWN.",
            strategy_decision,
            market_assessment,
            timestamp,
        )

    # From here: character is CONTESTED or CLEAR, confidence is a real
    # (non-UNKNOWN) value. Build the base verdict, then apply
    # modifiers. Confidence downgrades by at most one step total,
    # regardless of how many distinct warning reasons apply.
    warnings: List[str] = []
    controls: List[str] = []

    if character == "CONTESTED":
        risk_level = taxonomy.RISK_LEVEL_MODERATE
        warnings.append(taxonomy.WARNING_REASON_CONTESTED_MARKET)
        controls.append(taxonomy.CONTROL_MONITOR_MORE_FREQUENTLY)
    else:  # CLEAR
        if market_assessment.confidence in ("HIGH", "VERY_HIGH"):
            risk_level = taxonomy.RISK_LEVEL_LOW
        elif market_assessment.confidence == "MODERATE":
            risk_level = taxonomy.RISK_LEVEL_MODERATE
            warnings.append(taxonomy.WARNING_REASON_LOW_CONFIDENCE)
            controls.append(taxonomy.CONTROL_MONITOR_MORE_FREQUENTLY)
        else:  # LOW or VERY_LOW
            risk_level = taxonomy.RISK_LEVEL_HIGH
            warnings.append(taxonomy.WARNING_REASON_LOW_CONFIDENCE)
            controls.append(taxonomy.CONTROL_REDUCE_SIZE)
            controls.append(taxonomy.CONTROL_MONITOR_MORE_FREQUENTLY)

        if not market_assessment.supporting_evidence:
            warnings.append(taxonomy.WARNING_REASON_WEAK_EVIDENCE)
            if taxonomy.CONTROL_MONITOR_MORE_FREQUENTLY not in controls:
                controls.append(taxonomy.CONTROL_MONITOR_MORE_FREQUENTLY)

    if market_assessment.market_phase == "UNSTABLE":
        warnings.append(taxonomy.WARNING_REASON_HIGH_VARIABILITY)
        risk_level = _bump_risk_one_step(risk_level)
        if taxonomy.CONTROL_MONITOR_MORE_FREQUENTLY not in controls:
            controls.append(taxonomy.CONTROL_MONITOR_MORE_FREQUENTLY)

    if warnings:
        status = taxonomy.STATUS_APPROVED_WITH_WARNINGS
        approval = taxonomy.APPROVAL_ALLOW_WITH_CONTROLS
        confidence = _downgrade_one_step(market_assessment.confidence)
    else:
        status = taxonomy.STATUS_APPROVED
        approval = taxonomy.APPROVAL_ALLOW
        confidence = market_assessment.confidence

    trace_lines = [
        f"Strategy {strategy_decision.selected_strategy}.",
        f"Market Character {character}.",
        f"Confidence {market_assessment.confidence}.",
        f"Risk {risk_level}.",
    ]
    if warnings:
        trace_lines.append("Warnings: " + ", ".join(warnings) + ".")
    trace_lines.append(f"Approval {approval}.")
    trace = " ".join(trace_lines)

    seed = "|".join(
        [strategy_decision.decision_id, market_assessment.assessment_id, status, approval, timestamp]
    )
    assessment_id = "RA-" + hashlib.md5(seed.encode()).hexdigest()[:16]

    return RiskAssessment(
        assessment_id=assessment_id,
        status=status,
        risk_level=risk_level,
        approval=approval,
        blocking_reason=None,
        warning_reasons=tuple(warnings),
        required_controls=tuple(controls),
        confidence=confidence,
        decision_trace=trace,
        strategy_decision_id=strategy_decision.decision_id,
        market_state_assessment_id=market_assessment.assessment_id,
        timestamp=timestamp,
        version=taxonomy.RISK_BRAIN_VERSION,
    )
