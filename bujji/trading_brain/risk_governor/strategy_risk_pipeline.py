"""Strategy -> Risk Governor Integration Pipeline -- BUJJI Options OS
v3, Gate E.1, Parts 3-7.

PURPOSE: Strategy Engine (msi_trade_construction) -> Trade Proposal ->
Strategy Adapter -> Risk Governor Pipeline (D.6) -> Integrated
Decision. Deliberately "boring": this file contains almost no
business logic of its own -- it maps, orchestrates, and preserves
immutability. All intelligence stays in the Strategy Engine (what to
trade) and the Risk Governor (whether/how much to trade); this module
only guarantees every proposal flows through the established D.1-D.6
pipeline before anything downstream can consider it.

No new trading logic, no new risk model, no broker execution, no
duplicated calculation or threshold -- this file imports zero
threshold constants of its own and performs zero arithmetic on
proposal/market data (verified structurally in tests, same AST-scan
discipline as D.6's own module).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from bujji.msi_trade_construction.models import TradeConstructionAssessment

from .capital_safety_governor import CapitalSafetySnapshot, CapitalSafetyThresholds, ProposedTradeEffect
from .portfolio_risk_aggregator import PortfolioRiskThresholds
from .risk_budget_governor import RiskPolicy
from .position_lifecycle_intelligence import PositionHealthThresholds
from .adaptive_risk_memory import RiskMemoryEntry
from .risk_governor_pipeline import GovernorPipelineResult, run_risk_governor_pipeline
from .strategy_risk_adapter import (
    InvalidStrategyProposalError, adapt_strategy_proposal_to_governor_context,
)

Clock = Callable[[], datetime]

STAGE_PROPOSAL = "PROPOSAL"

INTEGRATED_APPROVED = "APPROVED"
INTEGRATED_BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class TraceStep:
    stage: str
    status: str
    detail: str


@dataclass(frozen=True)
class IntegratedDecisionTrace:
    steps: Tuple[TraceStep, ...]


@dataclass(frozen=True)
class IntegratedStrategyDecision:
    proposal: TradeConstructionAssessment            # the ORIGINAL proposal object, never a copy or mutation
    governor_result: Optional[GovernorPipelineResult]  # None only if stopped before the governor ran
    original_quantity: int                              # the caller-supplied desired_quantity, unmodified
    approved_quantity: int                                # what the Risk Governor approved (0 if blocked)
    final_quantity: int                                    # identical to approved_quantity in E.1 -- see below
    adaptive_adjustment: Optional[str]
    blocking_stage: Optional[str]                            # STAGE_PROPOSAL, or one of D.6's own STAGE_* constants, or None
    explanation: str
    decision_trace: IntegratedDecisionTrace


def run_strategy_risk_pipeline(
    proposal: TradeConstructionAssessment,
    desired_quantity: int,
    requested_risk: float,
    capital_snapshot: CapitalSafetySnapshot,
    proposed_trade_effect: ProposedTradeEffect,
    capital_safety_thresholds: Optional[CapitalSafetyThresholds],
    position_groups: List,
    margin_snapshot: Optional[object],
    margin_explanation: Optional[object],
    risk_by_position_group_id: Optional[Dict[str, float]],
    portfolio_risk_thresholds: Optional[PortfolioRiskThresholds],
    risk_policy: RiskPolicy,
    position_health_thresholds: Optional[PositionHealthThresholds],
    market_regime: Optional[str],
    memory_entries: Tuple[RiskMemoryEntry, ...],
    clock: Clock,
) -> IntegratedStrategyDecision:
    """Strategy Engine -> Trade Proposal -> Strategy Adapter -> Risk
    Governor Pipeline -> Integrated Decision. Part 6's failure-handling
    rules, all fail-closed, no exception ever becomes an approval:

      - proposal.constructed is False, or has no legs -> STOP before
        the governor runs at all (STAGE_PROPOSAL), approved/final
        quantity 0. Caught as InvalidStrategyProposalError from the
        adapter -- never allowed to propagate into a fallback approval.
      - the governor itself then runs EXACTLY once (a single call to
        run_risk_governor_pipeline, matching D.6's own "governors
        execute exactly once" discipline one level up)."""
    if not proposal.constructed or not proposal.legs:
        detail = f"Strategy proposal not usable: {proposal.rejection_reason or 'no legs constructed'}."
        return IntegratedStrategyDecision(
            proposal=proposal, governor_result=None, original_quantity=desired_quantity,
            approved_quantity=0, final_quantity=0, adaptive_adjustment=None, blocking_stage=STAGE_PROPOSAL,
            explanation=f"BLOCKED at {STAGE_PROPOSAL}: {detail}",
            decision_trace=IntegratedDecisionTrace((TraceStep(STAGE_PROPOSAL, "INVALID", detail),)),
        )

    try:
        context = adapt_strategy_proposal_to_governor_context(
            proposal, desired_quantity, requested_risk, capital_snapshot, proposed_trade_effect,
            capital_safety_thresholds, position_groups, margin_snapshot, margin_explanation,
            risk_by_position_group_id, portfolio_risk_thresholds, risk_policy, position_health_thresholds,
            market_regime, memory_entries, clock,
        )
    except InvalidStrategyProposalError as exc:
        detail = str(exc)
        return IntegratedStrategyDecision(
            proposal=proposal, governor_result=None, original_quantity=desired_quantity,
            approved_quantity=0, final_quantity=0, adaptive_adjustment=None, blocking_stage=STAGE_PROPOSAL,
            explanation=f"BLOCKED at {STAGE_PROPOSAL}: {detail}",
            decision_trace=IntegratedDecisionTrace((TraceStep(STAGE_PROPOSAL, "INVALID", detail),)),
        )

    governor_result = run_risk_governor_pipeline(context)

    proposal_step = TraceStep(
        STAGE_PROPOSAL, "VALID",
        f"Strategy proposal {proposal.assessment_id!r} ({proposal.strategy_family}) constructed with "
        f"{len(proposal.legs)} leg(s).",
    )
    governor_steps = tuple(
        TraceStep(s.stage, s.status, s.detail) for s in governor_result.decision_trace.steps
    )
    decision_trace = IntegratedDecisionTrace((proposal_step,) + governor_steps)

    if governor_result.blocking_stage is not None:
        blocking_step = next(s for s in governor_steps if s.stage == governor_result.blocking_stage)
        return IntegratedStrategyDecision(
            proposal=proposal, governor_result=governor_result, original_quantity=desired_quantity,
            approved_quantity=0, final_quantity=0,
            adaptive_adjustment=None, blocking_stage=governor_result.blocking_stage,
            explanation=f"BLOCKED at {governor_result.blocking_stage}: {blocking_step.detail}",
            decision_trace=decision_trace,
        )

    adaptive_adjustment = (
        governor_result.adaptive_decision.adaptive_recommendation if governor_result.adaptive_decision else None
    )
    return IntegratedStrategyDecision(
        proposal=proposal, governor_result=governor_result, original_quantity=desired_quantity,
        approved_quantity=governor_result.final_quantity, final_quantity=governor_result.final_quantity,
        adaptive_adjustment=adaptive_adjustment, blocking_stage=None,
        explanation=f"APPROVED at {governor_result.final_quantity} lots (originally requested "
                    f"{desired_quantity}). {governor_steps[-1].detail}",
        decision_trace=decision_trace,
    )


def explain_integrated_decision(decision: IntegratedStrategyDecision) -> str:
    """Strategy -> Risk -> Final outcome, using only decision.explanation
    (already built above from the proposal's own identity and the
    governor's own trace text) -- no new reasoning authored here."""
    return (
        f"Strategy: {decision.proposal.strategy_family} ({decision.proposal.assessment_id}). "
        f"{decision.explanation}"
    )
